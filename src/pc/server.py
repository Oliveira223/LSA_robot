"""
server.py — servidor TCP de texto (etapa "a" do pipeline de comunicação).

Escuta em HOST:PORTA, atende um cliente por vez e, para cada mensagem
recebida, devolve uma resposta processada. Aqui o "processamento" é só
texto.upper() — o servidor de verdade (áudio → transcrição → resposta)
é o server_voz.py. Este arquivo continua existindo como o caso mínimo
que valida o transporte (etapas a/b).

Uso (a partir de src/):
    python -m pc.server [host] [porta]
    (default: 127.0.0.1 5000)
"""

from __future__ import annotations

import socket
import sys

from common.protocol import recv_msg, send_texto


def processar(texto: str) -> str:
    """Placeholder do processamento real (ver server_voz.py)."""
    return texto.upper()


def atender(conexao: socket.socket) -> None:
    """Loop de mensagens de um cliente já conectado."""
    while True:
        try:
            msg = recv_msg(conexao)
        except (ConnectionResetError, ValueError) as e:
            print(f"[servidor] erro na conexao: {e}")
            return
        if msg is None:
            print("[servidor] cliente desconectou")
            return
        print(f"[servidor] recebido: {msg.texto!r}")
        send_texto(conexao, processar(msg.texto))


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
