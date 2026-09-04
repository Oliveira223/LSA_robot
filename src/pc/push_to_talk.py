"""
push_to_talk.py — teste local de STT com "ENTER para gravar / ENTER para parar".

Fluxo, em loop, sem encerrar:
  1. ENTER          → começa a gravar (mostra os segundos correndo);
  2. ENTER de novo  → para e transcreve (segundos correndo de novo);
  3. imprime a frase transcrita + uma linha de debug (duração, pico, tempo de STT).
'q' + ENTER, ou Ctrl-C, para sair.

Lê o teclado do stdin normal do terminal (linha a linha) — não depende de
X11/Wayland nem de biblioteca de teclado. Funciona em qualquer terminal,
inclusive por SSH.

Não usa rede nem socket: é só microfone → faster-whisper → terminal. Serve
para validar captação + transcrição numa máquina só, antes de plugar no
pipeline (pc/server_voz.py + jetson/audio_client.py).

Uso (a partir de src/):
    python -m pc.push_to_talk [indice_do_microfone]
    python -m pc.push_to_talk --list      # lista as entradas de áudio

Dependências (no PC):  pip install -r src/pc/requirements.txt
"""

from __future__ import annotations

import sys
import threading
import time

import numpy as np
import sounddevice as sd

from pc import stt

TAXA_PREFERIDA = 16000
CANAIS = 1


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

        self._stream = sd.InputStream(
            samplerate=self.taxa, channels=CANAIS, dtype="float32",
            device=indice_dispositivo, callback=self._callback,
        )
        self.nome = sd.query_devices(indice_dispositivo, "input")["name"]

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


def _preparar(audio: np.ndarray) -> tuple[np.ndarray, float, float]:
    """Normaliza sinal fraco. Devolve (audio_tratado, pico_original, ganho_aplicado)."""
    if audio.size == 0:
        return audio, 0.0, 1.0
    pico = float(np.abs(audio).max())
    ganho = 1.0
    if 0 < pico < 0.5:
        ganho = min(0.95 / pico, 30.0)   # teto evita amplificar só ruído
        audio = np.clip(audio * ganho, -1.0, 1.0)
    return audio, pico, ganho


def _ler(prompt: str) -> str | None:
    """input() que devolve None em EOF/Ctrl-C em vez de estourar."""
    try:
        return input(prompt)
    except (EOFError, KeyboardInterrupt):
        print()
        return None


def _listar_entradas():
    print("Microfones (entradas de áudio):")
    for i, d in enumerate(sd.query_devices()):
        if d["max_input_channels"] > 0:
            print(f"  [{i}] {d['name']}")


def _uma_rodada(grav: "Gravador") -> None:
    audio = grav.parar()
    dur = audio.size / grav.taxa
    tratado, pico, ganho = _preparar(audio)

    if dur < 0.3:
        print("  (muito curto)")
        return

    with Cronometro("  transcrevendo…") as cron:
        try:
            texto = stt.transcrever_array(tratado, taxa=grav.taxa)
        except stt.ErroDeSTT as e:
            print(f"\r  erro: {e}            ")
            return

    print(f'\r  "{texto}"' if texto else "\r  (nada reconhecido)", " " * 24)
    linha = f"  {dur:.1f}s · pico {pico:.2f}"
    if ganho > 1.01:
        linha += f" · ganho ×{ganho:.0f}"
    linha += f" · stt {cron.elapsed:.1f}s"
    print(linha)


def main() -> None:
    indice = None
    if len(sys.argv) > 1:
        if sys.argv[1] in ("-l", "--list"):
            _listar_entradas()
            return
        try:
            indice = int(sys.argv[1])
        except ValueError:
            print(f"[erro] índice de dispositivo inválido: {sys.argv[1]!r}\n")
            _listar_entradas()
            raise SystemExit(2)

    # Carrega o modelo já no início (a 1ª vez baixa).
    stt._obter_modelo()

    with Gravador(indice) as grav:
        print(f"\nmic: {grav.nome} · {grav.taxa} Hz · modelo {stt.MODELO}")
        print("ENTER grava / para · q sai\n")

        def _sair(s: str | None) -> bool:
            return s is None or s.strip().lower() in ("q", "sair", "quit", "exit")

        while True:
            if _sair(_ler("▶ gravar > ")):
                break

            grav.iniciar()
            with Cronometro("● gravando…", "  (ENTER para parar)"):
                parar = _ler("")
            if _sair(parar):
                grav.parar()
                break
            print()
            _uma_rodada(grav)
            print()

    print("[fim]")


if __name__ == "__main__":
    main()
