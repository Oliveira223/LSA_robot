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

import audioop
import io
import queue
import subprocess
import threading
import time
import wave
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

CARD_FALLBACK = "2"
SOURCE_FALLBACK = "alsa_input.usb-PrimeSense_PrimeSense_Device-01.analog-stereo"


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


def _achar_card():
    resultado = _rodar(["arecord", "-l"])
    if resultado is None:
        return CARD_FALLBACK
    saida = resultado.stdout.decode("utf-8", "replace")
    for linha in saida.splitlines():
        if linha.startswith("card ") and "PrimeSense" in linha:
            # "card 2: Device [USB Device 0x1d27:0x601], device 0: ..."
            return linha.split()[1].rstrip(":")
    return CARD_FALLBACK


def _achar_source():
    resultado = _rodar(["pactl", "list", "short", "sources"])
    if resultado is None:
        return SOURCE_FALLBACK
    saida = resultado.stdout.decode("utf-8", "replace")
    for linha in saida.splitlines():
        if "PrimeSense" in linha and "input" in linha:
            return linha.split()[1]
    return SOURCE_FALLBACK


def _aplicar_ganho(card, ganho):
    for numid in ("4", "5"):
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
                 ganho=GANHO_PADRAO, device=None):
        self._card = _achar_card()
        self._source = _achar_source()
        self._device = device or ("hw:%s,0" % self._card)
        self._limiar = limiar_fala
        self._silencio_s = silencio_s
        self._ganho = ganho

        _aplicar_ganho(self._card, ganho)
        _rodar(["pactl", "suspend-source", self._source, "1"])

        n_amostras_chunk = int(TAXA * CHUNK_S)
        self._bytes_por_chunk = n_amostras_chunk * CANAIS * LARGURA

        self._fila = queue.Queue()
        self._rodando = True
        self._estado_lock = threading.Lock()
        self._nivel_atual = 0
        self._gravando_atual = False
        self._vivo = True   # False quando desiste de religar o arecord (vira "MIC OFFLINE" na HUD)

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
            with self._estado_lock:
                self._nivel_atual = nivel
                self._gravando_atual = gravando

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
        try:
            item = self._fila.get(timeout=timeout)
        except queue.Empty:
            return None
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
