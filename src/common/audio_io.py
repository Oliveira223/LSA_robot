"""
audio_io.py — captura e reprodução de áudio compartilhadas entre o cliente
de voz da Jetson (jetson/audio_client.py) e a ferramenta local do PC
(pc/push_to_talk.py).

Fica em common/ porque os dois lados usam, mas — ao contrário de
common/protocol.py — depende de sounddevice + numpy. protocol.py continua
sem dependência externa: quem só troca texto (pc/server_chat.py,
jetson/client.py) não importa este módulo.

Conteúdo:
  - Gravador   : stream de entrada sempre aberto; acumula quadros só entre
                 iniciar() e parar() (push-to-talk de duração variável, sem
                 detecção de voz).
  - Cronometro : escreve os segundos correndo na mesma linha do terminal.
  - array_para_wav_bytes / tocar_wav_bytes : ponte com common/protocol.py,
                 que transporta AUDIO como os bytes de um arquivo WAV.
"""

from __future__ import annotations

import io
import sys
import threading
import time
import wave

import numpy as np
import sounddevice as sd

TAXA_PREFERIDA = 16000   # 16 kHz mono: cobre a fala e é o que o faster-whisper espera
CANAIS = 1
LARGURA_AMOSTRA = 2      # 2 bytes por amostra = int16


class ErroDeAudioIO(Exception):
    """Falha previsível de captura ou de reprodução."""


class Gravador:
    """Stream de entrada sempre aberto; acumula quadros só entre iniciar() e parar()."""

    def __init__(self, indice_dispositivo=None):
        self._quadros: list[np.ndarray] = []
        self._ativo = False
        self._lock = threading.Lock()

        try:
            sd.check_input_settings(device=indice_dispositivo, channels=CANAIS,
                                    samplerate=TAXA_PREFERIDA, dtype="float32")
            self.taxa = TAXA_PREFERIDA
        except Exception:
            info = sd.query_devices(indice_dispositivo, "input")
            self.taxa = int(info["default_samplerate"])

        try:
            self._stream = sd.InputStream(
                samplerate=self.taxa, channels=CANAIS, dtype="float32",
                device=indice_dispositivo, callback=self._callback,
            )
            self.nome = sd.query_devices(indice_dispositivo, "input")["name"]
        except Exception as e:
            raise ErroDeAudioIO(
                f"não foi possível abrir o microfone: {e}\n"
                "Rode 'python -m pc.push_to_talk --list' para ver as entradas."
            ) from e

    def _callback(self, indata, _frames, _time, status):
        if status:
            print(f"[mic] {status}", file=sys.stderr, flush=True)
        with self._lock:
            if self._ativo:
                self._quadros.append(indata.copy())

    def __enter__(self):
        self._stream.start()
        return self

    def __exit__(self, *_):
        self._stream.stop()
        self._stream.close()

    def iniciar(self):
        with self._lock:
            self._quadros.clear()
            self._ativo = True

    def parar(self) -> np.ndarray:
        with self._lock:
            self._ativo = False
            if not self._quadros:
                return np.zeros(0, dtype=np.float32)
            return np.concatenate(self._quadros).reshape(-1)


class Cronometro:
    """Escreve '<rótulo> N.Ns<sufixo>' na mesma linha até ser parado. `elapsed` no fim."""

    def __init__(self, rotulo: str, sufixo: str = ""):
        self._rotulo, self._sufixo = rotulo, sufixo
        self._parar = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self.elapsed = 0.0

    def _loop(self):
        while not self._parar.wait(0.1):
            e = time.monotonic() - self._t0
            print(f"\r{self._rotulo} {e:4.1f}s{self._sufixo}   ", end="", flush=True)

    def __enter__(self):
        self._t0 = time.monotonic()
        self._thread.start()
        return self

    def __exit__(self, *_):
        self._parar.set()
        self._thread.join(timeout=1)
        self.elapsed = time.monotonic() - self._t0


def array_para_wav_bytes(amostras: np.ndarray, taxa: int) -> bytes:
    """
    Converte um sinal float32 em [-1, 1] (mono) nos bytes de um arquivo WAV
    PCM 16-bit — o formato que common/protocol.py transporta como AUDIO e
    que o faster-whisper lê sem conversão extra.
    """
    amostras = np.asarray(amostras, dtype=np.float32).reshape(-1)
    inteiros = (np.clip(amostras, -1.0, 1.0) * 32767.0).astype("<i2")
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(CANAIS)
        wav.setsampwidth(LARGURA_AMOSTRA)
        wav.setframerate(int(taxa))
        wav.writeframes(inteiros.tobytes())
    return buffer.getvalue()


def tocar_wav_bytes(dados: bytes, bloquear: bool = True) -> None:
    """
    Toca os bytes de um arquivo WAV no dispositivo de saída padrão.

    Lê taxa/canais do próprio cabeçalho do WAV. Usado pelo cliente da Jetson
    para reproduzir a resposta em voz que o PC sintetizou (espeak-ng gera
    WAV PCM 16-bit).
    """
    try:
        with wave.open(io.BytesIO(dados), "rb") as wav:
            canais = wav.getnchannels()
            largura = wav.getsampwidth()
            taxa = wav.getframerate()
            quadros = wav.readframes(wav.getnframes())
    except wave.Error as e:
        raise ErroDeAudioIO(f"WAV inválido: {e}") from e

    if largura != 2:
        raise ErroDeAudioIO(f"esperava WAV PCM 16-bit, veio sampwidth={largura}")

    sinal = np.frombuffer(quadros, dtype="<i2").astype(np.float32) / 32768.0
    if canais > 1:
        sinal = sinal.reshape(-1, canais)

    try:
        sd.play(sinal, samplerate=taxa)
        if bloquear:
            sd.wait()
    except sd.PortAudioError as e:
        raise ErroDeAudioIO(f"falha ao tocar áudio: {e}") from e
