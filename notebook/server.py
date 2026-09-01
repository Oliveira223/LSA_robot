"""
server.py — servidor TCP de texto (etapa "a" do pipeline de comunicação).

Escuta em HOST:PORTA, atende um cliente por vez e, para cada mensagem
recebida, devolve uma resposta processada. Por enquanto o "processamento"
e' so' texto.upper() — um placeholder do que mais tarde sera' a transcricao
(STT) + IA rodando no PC.

Uso:
    python notebook/server.py [host] [porta]
    (default: 127.0.0.1 5000)
"""

from __future__ import annotations

import os
import socket
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "common"))
from protocol import recv_msg, send_msg  # noqa: E402


def processar(texto: str) -> str:
    """Placeholder da futura camada de STT + IA."""
    return texto.upper()


def atender(conexao: socket.socket) -> None:
    """Loop de mensagens de um cliente ja' conectado."""
    while True:
        try:
            texto = recv_msg(conexao)
        except (ConnectionResetError, ValueError) as e:
            print(f"[servidor] erro na conexao: {e}")
            return
        if texto is None:
            print("[servidor] cliente desconectou")
            return
        print(f"[servidor] recebido: {texto!r}")
        send_msg(conexao, processar(texto))


def main() -> None:
    host = sys.argv[1] if len(sys.argv) > 1 else "127.0.0.1"
    porta = int(sys.argv[2]) if len(sys.argv) > 2 else 5000

    servidor = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    # Permite reabrir a porta logo apos fechar o servidor, sem esperar o TIME_WAIT.
    servidor.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    servidor.bind((host, porta))
    servidor.listen(1)
    print(f"[servidor] escutando em {host}:{porta} (Ctrl-C para sair)")

    try:
        while True:
            conexao, endereco = servidor.accept()
            print(f"[servidor] cliente conectado: {endereco[0]}:{endereco[1]}")
            with conexao:
                atender(conexao)
            print("[servidor] aguardando novo cliente...")
    except KeyboardInterrupt:
        print("\n[servidor] encerrando")
    finally:
        servidor.close()


if __name__ == "__main__":
    main()
