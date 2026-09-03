"""
client.py — cliente TCP de texto (etapa "a" do pipeline de comunicacao).

Conecta em HOST:PORTA, le' linhas do teclado, envia cada uma como uma
mensagem e imprime a resposta do servidor. Roda no Raspberry Pi (ou
simulado no PC); por isso host e porta sao argumentos, e nao valores fixos.

O cliente de voz (grava do microfone em vez de ler do teclado) e' o
audio_client.py. Este arquivo continua como o caso minimo de teste do
transporte.

Uso (a partir de src/):
    python -m rasp.client [host] [porta]
    (default: 127.0.0.1 5000)
"""

from __future__ import annotations

import socket
import sys

from common.protocol import recv_msg, send_texto


def main() -> None:
    host = sys.argv[1] if len(sys.argv) > 1 else "127.0.0.1"
    porta = int(sys.argv[2]) if len(sys.argv) > 2 else 5000

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.connect((host, porta))
        print(f"[cliente] conectado em {host}:{porta} (Ctrl-C para sair)")
        try:
            while True:
                try:
                    texto = input("voce> ")
                except EOFError:        # fim do stdin (Ctrl-D ou pipe encerrado)
                    print()
                    break
                send_texto(sock, texto)
                msg = recv_msg(sock)
                if msg is None:
                    print("[cliente] servidor fechou a conexao")
                    break
                print(f"robo> {msg.texto}")
        except KeyboardInterrupt:
            print("\n[cliente] encerrando")


if __name__ == "__main__":
    main()
