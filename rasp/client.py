"""
client.py — cliente TCP de texto (etapa "a" do pipeline de comunicacao).

Conecta em HOST:PORTA, le' linhas do teclado, envia cada uma como uma
mensagem e imprime a resposta do servidor. Mais tarde este codigo roda no
Raspberry Pi; por isso host e porta sao argumentos, e nao valores fixos.

Uso:
    python rasp/client.py [host] [porta]
    (default: 127.0.0.1 5000)
"""

from __future__ import annotations

import os
import socket
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "common"))
from protocol import recv_msg, send_msg  # noqa: E402


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
                send_msg(sock, texto)
                resposta = recv_msg(sock)
                if resposta is None:
                    print("[cliente] servidor fechou a conexao")
                    break
                print(f"robo> {resposta}")
        except KeyboardInterrupt:
            print("\n[cliente] encerrando")


if __name__ == "__main__":
    main()
