"""
mic_vad.py — escuta contínua do microfone do PC com detecção de fala por
volume (VAD simples), pra pc/voice_client.py não precisar de ENTER/ENTER a
cada frase.

Mesma máquina de estados do jetson/mic_vad.py (pré-roll, limiar de RMS,
fecha a frase após silêncio contínuo, teto de segurança) — só a fonte do
áudio muda: aqui é sounddevice (o PC já usa em common/audio_io.py) em vez
de arecord/ALSA. O limiar de RMS é em amplitude float32 normalizada
([-1, 1]), não em unidades inteiras como no lado da Jetson — os dois não
são comparáveis diretamente.

Uso (a partir de src/):
    python3 -m pc.mic_vad                    # só escuta e imprime
    python3 -m pc.mic_vad --mic-limiar 0.03   # ajuste fino (padrão 0.02)
"""

from __future__ import annotations

import queue
import sys
import threading

import numpy as np
import sounddevice as sd

TAXA = 16000              # mesma taxa que pc/stt.py espera (ver common/audio_io.py)
CANAIS = 1

LIMIAR_FALA = 0.02        # RMS (float32, [-1,1]) acima disso = "está falando"
SILENCIO_S = 0.8          # silêncio contínuo por isso pra fechar a frase
PRE_ROLL_S = 0.3          # começo preservado antes do limiar disparar
MAX_FALA_S = 20.0         # teto de segurança — fecha a frase mesmo sem silêncio
CHUNK_S = 0.05            # granularidade de leitura/detecção (~50 ms)
N_CHUNKS_PRE_ROLL = max(1, int(PRE_ROLL_S / CHUNK_S))


class ErroDeMic(Exception):
    """Falha previsível ao abrir o microfone."""


class OuvinteVAD:
    """
    Escuta contínua com detecção de fala por volume.

    proxima_fala(timeout=None) bloqueia até capturar uma frase inteira (do
    momento que o RMS passa do limiar até um silêncio contínuo) e devolve
    um np.ndarray float32 mono — mesmo formato que Gravador.parar() devolve
    em common/audio_io.py, pronto pra pc.stt.transcrever_array. Devolve
    None se o timeout estourar, ou depois de parar().
    """

    def __init__(self, indice_dispositivo: int | None = None,
                 limiar_fala: float = LIMIAR_FALA, silencio_s: float = SILENCIO_S,
                 taxa: int = TAXA):
        self.taxa = taxa
        self._limiar = limiar_fala
        self._silencio_s = silencio_s
        self._fila = queue.Queue()
        self._fila_chunks: queue.Queue = queue.Queue()
        self._estado_lock = threading.Lock()
        self._nivel_atual = 0.0
        self._gravando_atual = False
        self._rodando = True

        tamanho_chunk = int(taxa * CHUNK_S)
        try:
            self._stream = sd.InputStream(
                samplerate=taxa, channels=CANAIS, dtype="float32",
                device=indice_dispositivo, blocksize=tamanho_chunk,
                callback=self._callback,
            )
            self.nome = sd.query_devices(indice_dispositivo, "input")["name"]
            self._stream.start()
        except Exception as e:
            raise ErroDeMic(f"não foi possível abrir o microfone: {e}") from e

        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def _callback(self, indata, _frames, _time, status):
        if status:
            print(f"[mic_vad] {status}", file=sys.stderr, flush=True)
        self._fila_chunks.put(indata.copy().reshape(-1))

    def estado(self):
        """(nivel_rms_atual, limiar, gravando) — pra depuração ao vivo."""
        with self._estado_lock:
            return self._nivel_atual, self._limiar, self._gravando_atual

    def _loop(self):
        pre_roll: list[np.ndarray] = []
        gravando = False
        buffer: list[np.ndarray] = []
        silencio_acumulado = 0.0
        duracao_fala = 0.0

        while self._rodando:
            try:
                pedaco = self._fila_chunks.get(timeout=0.5)
            except queue.Empty:
                continue

            nivel = float(np.sqrt(np.mean(np.square(pedaco)))) if pedaco.size else 0.0
            with self._estado_lock:
                self._nivel_atual = nivel
                self._gravando_atual = gravando

            if not gravando:
                pre_roll.append(pedaco)
                if len(pre_roll) > N_CHUNKS_PRE_ROLL:
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
                self._fila.put(np.concatenate(buffer))
                buffer = []
                pre_roll = []

    def proxima_fala(self, timeout: float | None = None) -> np.ndarray | None:
        """Devolve sempre a frase mais RECENTE — se quem consome (o loop de
        transcrição+resposta+fala) demorou, descarta o backlog em vez de
        responder a falas velhas (mesmo raciocínio de jetson/mic_vad.py)."""
        try:
            item = self._fila.get(timeout=timeout)
        except queue.Empty:
            return None
        while True:
            try:
                item = self._fila.get_nowait()
            except queue.Empty:
                break
        return item

    def parar(self):
        self._rodando = False
        try:
            self._stream.stop()
            self._stream.close()
        except Exception:
            pass
        self._thread.join(timeout=2)


if __name__ == "__main__":
    # Teste isolado: só escuta e imprime quando uma frase começa/termina, sem STT/rede.
    import argparse
    import time

    ap = argparse.ArgumentParser()
    ap.add_argument("indice_mic", nargs="?", type=int, default=None)
    ap.add_argument("--mic-limiar", type=float, default=LIMIAR_FALA)
    args = ap.parse_args()

    print("[mic_vad] abrindo microfone...")
    try:
        ouvinte = OuvinteVAD(args.indice_mic, limiar_fala=args.mic_limiar)
    except ErroDeMic as e:
        print(f"[mic_vad] {e}")
        raise SystemExit(1)

    print(f"[mic_vad] mic: {ouvinte.nome} · {ouvinte.taxa} Hz · limiar {args.mic_limiar} "
          "— fale perto do mic (Ctrl-C sai)")
    try:
        while True:
            t0 = time.time()
            audio = ouvinte.proxima_fala(timeout=1.0)
            if audio is None:
                continue
            dur = time.time() - t0
            pico = float(np.abs(audio).max()) if audio.size else 0.0
            print(f"[mic_vad] frase capturada: {audio.size} amostras "
                  f"(~{dur:.1f}s, pico {pico:.2f})")
    except KeyboardInterrupt:
        pass
    finally:
        ouvinte.parar()
        print("\n[mic_vad] encerrado")
