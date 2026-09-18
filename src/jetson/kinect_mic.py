"""
kinect_mic.py — array de microfones do Kinect (Xbox 360) via libfreenect.

DESCOBERTA em 2026-09-14, com hardware de verdade conectado: o audio do
Kinect NAO aparece como dispositivo ALSA (`arecord -l` nao mostra nada —
nenhum driver generico do kernel se associa a interface USB "Xbox NUI
Audio", diferente da PrimeSense). Precisa da API propria de audio da
libfreenect (`freenect_set_audio_in_callback`, ver
/usr/include/libfreenect_audio.h depois de instalar libfreenect-dev), que
entrega por callback 4 canais de mic crus (32-bit PCM, 16kHz) mais 1 canal
ja com cancelamento de ruido (16-bit PCM, 16kHz — o mais parecido com o
que o resto do pipeline ja espera, entao e o que usamos aqui).

Por isso este arquivo NAO reaproveita jetson.mic_vad.OuvinteVAD (que le
bytes de um `arecord` em subprocess) — a fonte dos dados e fundamentalmente
diferente (callback assincrono da libfreenect, nao um pipe bloqueante).
Reimplementa a mesma maquina de estados de VAD (limiar de RMS + pre-roll +
fecha a frase apos silencio continuo) alimentada pelo callback.

Interface publica identica a jetson.mic_vad.OuvinteVAD — estado(),
proxima_fala(), amostras_recentes(), parar() — dá pra trocar um pelo outro
em jetson/chat_client.py sem mudar mais nada la.

IMPORTANTE: assim como jetson/kinect_camera.py, cada modulo abre seu
PROPRIO freenect_device — dois processos (kinect_mic.py sozinho +
kinect_camera.py sozinho) NAO conseguem abrir o Kinect ao mesmo tempo (USB
so aceita 1 dono). Pra usar video+audio juntos de verdade mais adiante,
precisa compartilhar o mesmo SensorKinect/freenect_device entre os dois —
ainda nao feito, fora de escopo do teste de hoje.

LIMIAR_FALA abaixo e um CHUTE (mesma ordem de grandeza do que a PrimeSense
usa) — calibrar depois de testar com hardware de verdade (o
`python3 -m jetson.kinect_mic` imprime o nivel RMS, use ele pra ajustar).

Compatibilidade: Python 3.6 da Jetson.

Uso (a partir de src/):
    python3 -m jetson.kinect_mic     # so escuta e imprime quando uma frase comeca/termina
"""

import array
import audioop
import ctypes
import io
import queue
import threading
import time
import wave
from collections import deque

from jetson.kinect_camera import ErroDeKinect, localizar_libfreenect

TAXA = 16000              # freenect entrega o canal 'cancelled' fixo em 16kHz
LARGURA = 2                # bytes por amostra (int16)

LIMIAR_FALA = 1200         # RMS acima disso = "esta falando" — CHUTE, calibrar (ver docstring)
SILENCIO_S = 0.8
PRE_ROLL_S = 0.3
MAX_FALA_S = 20.0
HISTORICO_AUDIO_S = 2.0

ErroDeMic = ErroDeKinect    # alias — mesma excecao que jetson.mic_vad.ErroDeMic representaria


_AudioInCallback = ctypes.CFUNCTYPE(
    None, ctypes.c_void_p, ctypes.c_int,
    ctypes.POINTER(ctypes.c_int32), ctypes.POINTER(ctypes.c_int32),
    ctypes.POINTER(ctypes.c_int32), ctypes.POINTER(ctypes.c_int32),
    ctypes.POINTER(ctypes.c_int16), ctypes.c_void_p)


def _carregar_lib():
    caminho = localizar_libfreenect()
    if not caminho:
        raise ErroDeKinect(
            "libfreenect nao encontrada — instale com "
            "'sudo apt install -y libfreenect0.5 libfreenect-dev libfreenect-bin'")
    lib = ctypes.CDLL(caminho)

    lib.freenect_init.argtypes = [ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p]
    lib.freenect_init.restype = ctypes.c_int
    lib.freenect_shutdown.argtypes = [ctypes.c_void_p]
    lib.freenect_shutdown.restype = ctypes.c_int
    lib.freenect_process_events.argtypes = [ctypes.c_void_p]
    lib.freenect_process_events.restype = ctypes.c_int
    lib.freenect_open_device.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p), ctypes.c_int]
    lib.freenect_open_device.restype = ctypes.c_int
    lib.freenect_close_device.argtypes = [ctypes.c_void_p]
    lib.freenect_close_device.restype = ctypes.c_int

    lib.freenect_set_audio_in_callback.argtypes = [ctypes.c_void_p, _AudioInCallback]
    lib.freenect_set_audio_in_callback.restype = None
    lib.freenect_start_audio.argtypes = [ctypes.c_void_p]
    lib.freenect_start_audio.restype = ctypes.c_int
    lib.freenect_stop_audio.argtypes = [ctypes.c_void_p]
    lib.freenect_stop_audio.restype = ctypes.c_int
    return lib


class OuvinteKinect:
    """Escuta continua do array de mic do Kinect com deteccao de fala por
    volume — mesma ideia de jetson.mic_vad.OuvinteVAD, fonte diferente."""

    def __init__(self, limiar_fala=LIMIAR_FALA, silencio_s=SILENCIO_S, indice=0):
        self._lib = _carregar_lib()

        ctx = ctypes.c_void_p()
        if self._lib.freenect_init(ctypes.byref(ctx), None) < 0:
            raise ErroDeKinect("freenect_init falhou")
        self._ctx = ctx

        dev = ctypes.c_void_p()
        if self._lib.freenect_open_device(self._ctx, ctypes.byref(dev), indice) < 0:
            self._lib.freenect_shutdown(self._ctx)
            raise ErroDeKinect(
                "nao foi possivel abrir o Kinect indice %d (esta ligado na "
                "fonte de energia externa, nao so no USB? outro processo "
                "ja esta com o dispositivo aberto?)" % indice)
        self._dev = dev

        self._limiar = limiar_fala
        self._silencio_s = silencio_s

        n_chunks_historico = max(1, int(HISTORICO_AUDIO_S * TAXA / 512))  # ~512 amostras/callback
        self._historico_audio = deque(maxlen=n_chunks_historico)
        self._fila = queue.Queue()
        self._estado_lock = threading.Lock()
        self._nivel_atual = 0
        self._gravando_atual = False
        self._vivo = True

        # estado da maquina de VAD — so tocado pela thread de eventos (o
        # callback sempre roda dentro de freenect_process_events(), um
        # unico thread), entao nao precisa de lock aqui
        self._pre_roll = []
        self._tam_pre_roll = max(1, int(PRE_ROLL_S * TAXA / 512))
        self._gravando = False
        self._buffer = []
        self._silencio_acumulado = 0.0
        self._duracao_fala = 0.0

        # precisa manter referencia forte ao CFUNCTYPE — ctypes nao segura
        # sozinho, callback coletado pelo GC vira crash silencioso
        self._cb_audio = _AudioInCallback(self._on_audio)
        self._lib.freenect_set_audio_in_callback(self._dev, self._cb_audio)

        if self._lib.freenect_start_audio(self._dev) < 0:
            raise ErroDeKinect("freenect_start_audio falhou")

        self._rodando = True
        self._thread = threading.Thread(target=self._loop_eventos, daemon=True)
        self._thread.start()

    def _on_audio(self, dev, num_samples, mic1, mic2, mic3, mic4, cancelled, unknown):
        pedaco = ctypes.string_at(cancelled, num_samples * LARGURA)
        nivel = audioop.rms(pedaco, LARGURA)
        duracao_chunk = num_samples / float(TAXA)

        with self._estado_lock:
            self._nivel_atual = nivel
            self._gravando_atual = self._gravando
            self._historico_audio.append(pedaco)

        if not self._gravando:
            self._pre_roll.append(pedaco)
            if len(self._pre_roll) > self._tam_pre_roll:
                self._pre_roll.pop(0)
            if nivel >= self._limiar:
                self._gravando = True
                self._buffer = list(self._pre_roll)
                self._silencio_acumulado = 0.0
                self._duracao_fala = len(self._buffer) * duracao_chunk
            return

        self._buffer.append(pedaco)
        self._duracao_fala += duracao_chunk
        if nivel < self._limiar:
            self._silencio_acumulado += duracao_chunk
        else:
            self._silencio_acumulado = 0.0

        if self._silencio_acumulado >= self._silencio_s or self._duracao_fala >= MAX_FALA_S:
            self._gravando = False
            self._fila.put(self._empacotar(self._buffer))
            self._buffer = []
            self._pre_roll = []

    @staticmethod
    def _empacotar(pedacos):
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(LARGURA)
            w.setframerate(TAXA)
            w.writeframes(b"".join(pedacos))
        return buf.getvalue()

    def _loop_eventos(self):
        while self._rodando:
            ret = self._lib.freenect_process_events(self._ctx)
            if ret < 0:
                with self._estado_lock:
                    self._vivo = False
                print("[kinect_mic] freenect_process_events erro (%d) — mic offline" % ret,
                      flush=True)
                break

    def estado(self):
        """(nivel_rms_atual, limiar, gravando, vivo) — mesma assinatura de
        jetson.mic_vad.OuvinteVAD.estado(), pra HUD/depuracao ao vivo."""
        with self._estado_lock:
            return self._nivel_atual, self._limiar, self._gravando_atual, self._vivo

    def amostras_recentes(self):
        """Ultimos ~HISTORICO_AUDIO_S segundos de audio (mono, int16), mais
        recente no final — mesma assinatura de OuvinteVAD.amostras_recentes()."""
        with self._estado_lock:
            pedacos = list(self._historico_audio)
        amostras = array.array("h")
        for pedaco in pedacos:
            amostras.frombytes(pedaco)
        return amostras

    def proxima_fala(self, timeout=None):
        """Devolve sempre a frase mais RECENTE (descarta backlog acumulado
        — mesmo motivo de jetson.mic_vad.OuvinteVAD.proxima_fala(), ver o
        comentario la)."""
        try:
            item = self._fila.get(timeout=timeout)
        except queue.Empty:
            return None
        while True:
            try:
                mais_novo = self._fila.get_nowait()
            except queue.Empty:
                break
            item = mais_novo
        return item

    def parar(self):
        self._rodando = False
        try:
            self._lib.freenect_stop_audio(self._dev)
        except Exception:
            pass
        self._thread.join(timeout=2)
        try:
            self._lib.freenect_close_device(self._dev)
        except Exception:
            pass
        try:
            self._lib.freenect_shutdown(self._ctx)
        except Exception:
            pass
        self._fila.put(None)  # sentinela: acorda quem estiver em proxima_fala()


if __name__ == "__main__":
    # Teste isolado: so escuta e imprime, sem rede nem GUI. Precisa do
    # Kinect ligado (USB + fonte de energia externa) e nada mais com ele
    # aberto ao mesmo tempo (nem kinect_camera.py).
    print("[kinect_mic] abrindo o Kinect...", flush=True)
    try:
        ouvinte = OuvinteKinect()
    except ErroDeKinect as e:
        print("[kinect_mic] %s" % e, flush=True)
        raise SystemExit(1)
    print("[kinect_mic] aberto — fale perto do array de mic (Ctrl-C sai)", flush=True)
    try:
        ultimo_print = 0.0
        while True:
            wav = ouvinte.proxima_fala(timeout=0.2)
            if wav is not None:
                print("[kinect_mic] frase capturada: %d bytes" % len(wav), flush=True)
                continue
            _, limiar, _, vivo = ouvinte.estado()
            if not vivo:
                print("[kinect_mic] mic offline — encerrando", flush=True)
                break
            agora = time.time()
            if agora - ultimo_print > 1.0:
                nivel, _, gravando, _ = ouvinte.estado()
                print("[kinect_mic] nivel RMS atual: %d (limiar=%d, gravando=%s)"
                      % (nivel, limiar, gravando), flush=True)
                ultimo_print = agora
    except KeyboardInterrupt:
        pass
    finally:
        ouvinte.parar()
        print("\n[kinect_mic] encerrado", flush=True)
