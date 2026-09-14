"""
audio_client.py — cliente de voz da Jetson (etapa d2).

Troca o teclado do client.py pelo microfone e pelo speaker:
  1. ENTER começa a gravar, ENTER de novo para (push-to-talk de duração
     variável — sem detecção de voz ainda); 'q' sai;
  2. envia o WAV para o servidor (mensagem AUDIO);
  3. recebe a resposta — normalmente já em voz (WAV) — e toca no speaker.
     Se vier texto (fallback quando o PC está sem espeak-ng), só imprime.

A Jetson faz o mínimo: grava, manda, toca. Transcrição, "cérebro" e
síntese de voz são todos do PC.

Testando localmente: rode no mesmo PC do server_voz — fala no microfone do
notebook, ouve pelos alto-falantes dele.

Uso (a partir de src/):
    python -m jetson.audio_client [host] [porta] [indice_do_microfone]
    (default: 127.0.0.1 5000, microfone padrão)
"""

from __future__ import annotations

import socket
import sys

from common import audio_io
from common.protocol import AUDIO, recv_msg, send_audio

DUR_MINIMA_S = 0.3   # abaixo disso foi tecla batida sem querer, não fala


def _ler(prompt: str) -> str | None:
    """input() que devolve None em EOF/Ctrl-C em vez de estourar."""
    try:
        return input(prompt)
    except (EOFError, KeyboardInterrupt):
        print()
        return None


def _sair(s: str | None) -> bool:
    return s is None or s.strip().lower() in ("q", "sair", "quit", "exit")


def _uma_rodada(sock: socket.socket, grav: "audio_io.Gravador") -> bool:
    """Uma gravação + envio + resposta. Devolve False quando é hora de sair."""
    if _sair(_ler("▶ gravar > ")):
        return False

    grav.iniciar()
    with audio_io.Cronometro("● gravando…", "  (ENTER para parar)"):
        parar = _ler("")
    if _sair(parar):
        grav.parar()
        return False
    print()

    amostras = grav.parar()
    dur = amostras.size / grav.taxa
    if dur < DUR_MINIMA_S:
        print("[cliente] muito curto — não enviado\n")
        return True

    wav = audio_io.array_para_wav_bytes(amostras, grav.taxa)
    print(f"[cliente] enviando {dur:.1f}s ({len(wav)} bytes)…")
    send_audio(sock, wav)

    msg = recv_msg(sock)
    if msg is None:
        print("[cliente] servidor fechou a conexao")
        return False

    if msg.tipo == AUDIO:
        print(f"[cliente] resposta em voz: {len(msg.dados)} bytes — tocando…")
        try:
            audio_io.tocar_wav_bytes(msg.dados)
        except audio_io.ErroDeAudioIO as e:
            print(f"[cliente] erro ao tocar: {e}")
    else:
        print(f"robo> {msg.texto}")
    print()
    return True


def main() -> None:
    host = sys.argv[1] if len(sys.argv) > 1 else "127.0.0.1"
    porta = int(sys.argv[2]) if len(sys.argv) > 2 else 5000
    indice_mic = int(sys.argv[3]) if len(sys.argv) > 3 else None

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.connect((host, porta))
        print(f"[cliente] conectado em {host}:{porta}")

        try:
            grav = audio_io.Gravador(indice_mic)
        except audio_io.ErroDeAudioIO as e:
            print(f"[cliente] {e}")
            raise SystemExit(1)

        with grav:
            print(f"[cliente] mic: {grav.nome} · {grav.taxa} Hz")
            print("[cliente] ENTER grava / ENTER para · q sai\n")
            try:
                while _uma_rodada(sock, grav):
                    pass
            except KeyboardInterrupt:
                print("\n[cliente] encerrando")


if __name__ == "__main__":
    main()
