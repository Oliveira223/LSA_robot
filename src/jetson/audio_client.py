"""
audio_client.py — cliente de voz da Jetson (etapa d).

Substitui o teclado do client.py pelo microfone:
  1. Enter para gravar 5 s (push-to-talk simples, sem detecção de voz ainda);
  2. envia o WAV para o servidor;
  3. imprime a resposta que volta em texto.

A Jetson faz o mínimo: grava, manda, mostra. Transcrição e "cérebro" são do
PC. A síntese de voz (TTS) da resposta entra numa sub-etapa posterior; por
enquanto a resposta só é impressa.

Ainda não usado: a Jetson atual não tem microfone plugado. Enquanto isso, o
transporte de texto (client.py + pc/server_chat.py) faz o papel de troca de
mensagens; este arquivo fica pronto para o dia em que o áudio entrar.

Uso (a partir de src/):
    python -m jetson.audio_client [host] [porta]
    (default: 127.0.0.1 5000)
"""

from __future__ import annotations

import socket
import sys

from common.protocol import recv_msg, send_audio
from jetson import audio

DURACAO_S = 5


def main() -> None:
    host = sys.argv[1] if len(sys.argv) > 1 else "127.0.0.1"
    porta = int(sys.argv[2]) if len(sys.argv) > 2 else 5000

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.connect((host, porta))
        print(f"[cliente] conectado em {host}:{porta} (Ctrl-C para sair)")

        try:
            while True:
                try:
                    input(f"[enter para gravar {DURACAO_S}s] ")
                except EOFError:
                    print()
                    break

                try:
                    caminho, rms = audio.gravar(duracao_segundos=DURACAO_S)
                except audio.ErroDeAudio as e:
                    print(f"[cliente] erro de audio: {e}")
                    continue

                try:
                    if audio.esta_silencioso(rms):
                        print(f"[cliente] nada captado (RMS={rms:.0f}) — nao enviado")
                        continue
                    with open(caminho, "rb") as f:
                        dados = f.read()
                finally:
                    audio.remover_arquivo(caminho)

                send_audio(sock, dados)
                msg = recv_msg(sock)
                if msg is None:
                    print("[cliente] servidor fechou a conexao")
                    break
                print(f"robo> {msg.texto}")
        except KeyboardInterrupt:
            print("\n[cliente] encerrando")


if __name__ == "__main__":
    main()
