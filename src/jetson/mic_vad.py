"""
mic_vad.py — escuta continua do microfone da PrimeSense com deteccao de
fala por volume (VAD simples), pra Jetson escutar sozinha sem teclado.

Mesma receita ja comprovada em ~/dev/mic-test (bring-up da PrimeSense nessa
Jetson): acha a placa/fonte certa por nome, suspende a fonte do PulseAudio
antes de gravar (senao "device busy" ou silencio puro), grava direto do
ALSA hw (S16_LE 48k 2ch) via `arecord` em subprocess, e ajusta o ganho de
captura da placa (o default de fabrica satura em voz normal).

Compatibilidade: escrito pra rodar no Python 3.6 da Jetson (sem
"from __future__ import annotations", sem "X | None") — ver PROGRESSO.md /
a descoberta de que nenhum common/jetson/*.py rodava de verdade aqui antes
desse fix.

Uso (a partir de src/):
    python3 -m jetson.mic_vad     # so escuta e imprime quando uma frase comeca/termina
"""

import array
import audioop
import io
import queue
import subprocess
import threading
import time
import wave
from collections import deque
from typing import Optional

TAXA = 48000
CANAIS = 2
LARGURA = 2  # bytes por amostra (S16_LE)

GANHO_PADRAO = 1700       # 0-4182; default de fabrica (3576) satura em voz normal
LIMIAR_FALA = 600         # RMS acima disso = "esta falando" (ajuste se precisar)
SILENCIO_S = 0.8          # silencio continuo por isso pra fechar a frase
PRE_ROLL_S = 0.3          # comeco preservado antes do limiar disparar
MAX_FALA_S = 20.0         # teto de seguranca — fecha a frase mesmo sem silencio
CHUNK_S = 0.05            # granularidade de leitura/deteccao (~50 ms)
HISTORICO_AUDIO_S = 2.0   # quanto de audio cru fica disponivel pra HUD (forma de onda)
N_CHUNKS_HISTORICO = max(1, int(HISTORICO_AUDIO_S / CHUNK_S))

CARD_FALLBACK = "2"
SOURCE_FALLBACK = "alsa_input.usb-PrimeSense_PrimeSense_Device-01.analog-stereo"
NOME_DISPOSITIVO_PADRAO = "PrimeSense"
NUMIDS_GANHO_PADRAO = ("4", "5")  # controles de ganho de captura da PrimeSense


class ErroDeMic(Exception):
    """Falha previsivel ao achar/abrir o microfone da PrimeSense."""


TIMEOUT_CMD_S = 3  # arecord/pactl/amixer as vezes travam de verdade nessa
                    # Jetson quando a interface USB de audio da PrimeSense
                    # esta capenga (visto na pratica 2026-09-14) — sem teto
                    # aqui, um comando travado prendia o app inteiro pra
                    # sempre, antes ate de abrir a janela.


def _rodar(cmd):
    try:
        return subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               timeout=TIMEOUT_CMD_S)
    except subprocess.TimeoutExpired:
        print("[mic_vad] comando travou (%s) — seguindo com o fallback"
              % " ".join(cmd), flush=True)
        return None


def _achar_card(nome_dispositivo=NOME_DISPOSITIVO_PADRAO, fallback=CARD_FALLBACK):
    resultado = _rodar(["arecord", "-l"])
    if resultado is None:
        return fallback
    saida = resultado.stdout.decode("utf-8", "replace")
    for linha in saida.splitlines():
        if linha.startswith("card ") and nome_dispositivo in linha:
            # "card 2: Device [USB Device 0x1d27:0x601], device 0: ..."
            return linha.split()[1].rstrip(":")
    return fallback


def _achar_source(nome_dispositivo=NOME_DISPOSITIVO_PADRAO, fallback=None):
    """`fallback=None` (padrao pra dispositivo desconhecido, ex.: Kinect) faz
    devolver None em vez de arriscar suspender a fonte de OUTRO dispositivo
    por engano — quem chama deve pular o suspend-source nesse caso."""
    resultado = _rodar(["pactl", "list", "short", "sources"])
    if resultado is None:
        return fallback
    saida = resultado.stdout.decode("utf-8", "replace")
    for linha in saida.splitlines():
        if nome_dispositivo in linha and "input" in linha:
            return linha.split()[1]
    return fallback


def _aplicar_ganho(card, ganho, numids=NUMIDS_GANHO_PADRAO):
    for numid in numids:
        _rodar(["amixer", "-c", card, "cset", "numid=%s" % numid, str(ganho)])


class OuvinteVAD:
    """
    Escuta continua com deteccao de fala por volume.

    proxima_fala(timeout=None) bloqueia ate capturar uma frase inteira (do
    momento que o volume passa do limiar ate um silencio continuo) e
    devolve os bytes de um WAV pronto pra mandar pelo protocolo. Devolve
    None se o timeout estourar, ou depois de parar().
    """

    MAX_RECONEXOES = 5        # tentativas de religar o arecord antes de desistir
    BACKOFF_RECONEXAO_S = 0.5

    def __init__(self, limiar_fala=LIMIAR_FALA, silencio_s=SILENCIO_S,
                 ganho=GANHO_PADRAO, device=None,
                 nome_dispositivo=NOME_DISPOSITIVO_PADRAO,
                 card_fallback=CARD_FALLBACK, source_fallback=SOURCE_FALLBACK,
                 numids_ganho=NUMIDS_GANHO_PADRAO):
        # nome_dispositivo/fallbacks/numids_ganho existem pra reaproveitar essa
        # classe com OUTRO sensor (ex.: jetson/kinect_mic.py) sem duplicar toda
        # a logica de VAD/reconexao/buffer — so muda como acha a placa e como
        # aplica o ganho. Os padroes preservam o comportamento original (PrimeSense).
        self._card = _achar_card(nome_dispositivo, card_fallback)
        self._source = _achar_source(nome_dispositivo, source_fallback)
        self._device = device or ("hw:%s,0" % self._card)
        self._limiar = limiar_fala
        self._silencio_s = silencio_s
        self._ganho = ganho

        if numids_ganho:
            _aplicar_ganho(self._card, ganho, numids_ganho)
        if self._source:
            _rodar(["pactl", "suspend-source", self._source, "1"])

        n_amostras_chunk = int(TAXA * CHUNK_S)
        self._bytes_por_chunk = n_amostras_chunk * CANAIS * LARGURA

        self._fila = queue.Queue()
        self._rodando = True
        self._estado_lock = threading.Lock()
        self._nivel_atual = 0
        self._gravando_atual = False
        self._vivo = True   # False quando desiste de religar o arecord (vira "MIC OFFLINE" na HUD)
        self._historico_audio = deque(maxlen=N_CHUNKS_HISTORICO)  # amostras cruas p/ forma de onda

        try:
            self._proc = self._abrir_arecord()
        except OSError as e:
            _rodar(["pactl", "suspend-source", self._source, "0"])
            raise ErroDeMic("nao foi possivel abrir 'arecord': %s" % e) from e

        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def _abrir_arecord(self):
        return subprocess.Popen(
            ["arecord", "-q", "-D", self._device, "-f", "S16_LE",
             "-r", str(TAXA), "-c", str(CANAIS), "-t", "raw"],
            stdout=subprocess.PIPE, stdin=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )

    def estado(self):
        """(nivel_rms_atual, limiar, gravando, vivo) — pra HUD/depuracao ao vivo.
        `vivo=False` quer dizer que o arecord morreu e nao conseguiu religar
        (ex.: sensor desconectado) — a leitura ficou parada de verdade, nao
        e so silencio."""
        with self._estado_lock:
            return self._nivel_atual, self._limiar, self._gravando_atual, self._vivo

    def amostras_recentes(self):
        """Ultimos ~HISTORICO_AUDIO_S segundos de audio cru (mono, int16),
        mais recente no final — pra desenhar uma forma de onda de verdade
        (picos reais, nao so o RMS agregado) na HUD da camera. Devolve um
        array.array('h'), vazio se ainda nao capturou nada."""
        with self._estado_lock:
            pedacos = list(self._historico_audio)
        amostras = array.array("h")
        for pedaco in pedacos:
            amostras.extend(pedaco)
        return amostras

    def _loop(self):
        pre_roll = []
        tam_pre_roll = max(1, int(PRE_ROLL_S / CHUNK_S))
        gravando = False
        buffer = []
        silencio_acumulado = 0.0
        duracao_fala = 0.0

        reconexoes = 0
        while self._rodando:
            pedaco = self._proc.stdout.read(self._bytes_por_chunk)
            if not pedaco:
                # arecord morreu (estouro de buffer, replug, kill externo...).
                # Nao contava com stderr antes: agora le o que sobrou pra
                # deixar rastro no log em vez de simplesmente sumir.
                erro = self._proc.stderr.read().decode("utf-8", "replace").strip()
                self._proc.wait(timeout=1)
                if not self._rodando:
                    break
                reconexoes += 1
                print("[mic_vad] arecord caiu (%s) — tentativa %d/%d de religar"
                      % (erro or "sem stderr", reconexoes, self.MAX_RECONEXOES), flush=True)
                if reconexoes > self.MAX_RECONEXOES:
                    with self._estado_lock:
                        self._vivo = False
                    print("[mic_vad] desisti de religar o arecord — mic offline", flush=True)
                    break
                time.sleep(self.BACKOFF_RECONEXAO_S)
                gravando = False
                buffer = []
                pre_roll = []
                try:
                    self._proc = self._abrir_arecord()
                except OSError as e:
                    print("[mic_vad] falha ao religar arecord: %s" % e, flush=True)
                    continue
                continue
            reconexoes = 0
            nivel = audioop.rms(pedaco, LARGURA)
            mono = array.array("h")
            mono.frombytes(audioop.tomono(pedaco, LARGURA, 0.5, 0.5))
            with self._estado_lock:
                self._nivel_atual = nivel
                self._gravando_atual = gravando
                self._historico_audio.append(mono)

            if not gravando:
                pre_roll.append(pedaco)
                if len(pre_roll) > tam_pre_roll:
                    pre_roll.pop(0)
                if nivel >= self._limiar:
                    gravando = True
                    buffer = list(pre_roll)
                    silencio_acumulado = 0.0
                    duracao_fala = len(buffer) * CHUNK_S
                continue

            buffer.append(pedaco)
            duracao_fala += CHUNK_S
            if nivel < self._limiar:
                silencio_acumulado += CHUNK_S
            else:
                silencio_acumulado = 0.0

            if silencio_acumulado >= self._silencio_s or duracao_fala >= MAX_FALA_S:
                gravando = False
                self._fila.put(self._empacotar(buffer))
                buffer = []
                pre_roll = []

        self._fila.put(None)  # sentinela: acorda quem estiver em proxima_fala()

    @staticmethod
    def _empacotar(pedacos):
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(CANAIS)
            w.setsampwidth(LARGURA)
            w.setframerate(TAXA)
            w.writeframes(b"".join(pedacos))
        return buf.getvalue()

    def proxima_fala(self, timeout=None):
        # type: (Optional[float]) -> Optional[bytes]
        """Devolve sempre a frase mais RECENTE. Se quem consome demorou (o
        `chat_client` so volta a chamar isso depois de mandar pro servidor,
        receber a resposta e falar ela — um ciclo que pode levar varios
        segundos), o VAD continua escutando e enfileirando frases novas
        nesse meio tempo. Sem descartar aqui, o consumidor ficaria
        respondendo a um backlog de audio velho em vez do que acabou de ser
        dito — visto na pratica 2026-09-14 (depois de uma reconexao longa,
        o chat respondeu a falas de minutos atras em vez da mais recente)."""
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
            self._proc.terminate()
            self._proc.wait(timeout=2)
        except Exception:
            try:
                self._proc.kill()
            except Exception:
                pass
        self._thread.join(timeout=2)
        if self._source:
            _rodar(["pactl", "suspend-source", self._source, "0"])


if __name__ == "__main__":
    # Teste isolado: so escuta e imprime, sem rede nem GUI.
    import sys
    import time

    print("[mic_vad] procurando a PrimeSense...", flush=True)
    ouvinte = OuvinteVAD()
    print("[mic_vad] device=%s source=%s — fale perto do mic (Ctrl-C sai)"
          % (ouvinte._device, ouvinte._source), flush=True)
    try:
        while True:
            t0 = time.time()
            wav = ouvinte.proxima_fala(timeout=1.0)
            if wav is None:
                _, _, _, vivo = ouvinte.estado()
                if not vivo:
                    print("[mic_vad] mic offline (arecord nao religou) — encerrando", flush=True)
                    break
                continue
            dur = time.time() - t0
            print("[mic_vad] frase capturada: %d bytes (~%.1fs)" % (len(wav), dur), flush=True)
    except KeyboardInterrupt:
        pass
    finally:
        ouvinte.parar()
        print("\n[mic_vad] encerrado", flush=True)
