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

import numpy as np
import sounddevice as sd

from common.audio_io import Cronometro, Gravador
from pc import stt


def preparar_audio(audio: np.ndarray) -> tuple[np.ndarray, float, float]:
    """Normaliza sinal fraco. Devolve (audio_tratado, pico_original, ganho_aplicado).

    Reaproveitado por pc/voice_client.py — mesmo problema de mic fraco.
    """
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
    tratado, pico, ganho = preparar_audio(audio)

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
