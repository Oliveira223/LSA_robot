"""
server_chat.py — servidor de "chat" Jetson ↔ PC: só texto, sem áudio.

A Jetson ainda não tem microfone nem alto-falante. Enquanto isso, este
servidor faz o papel que o áudio vai fazer depois: alguém escreve uma
mensagem no terminal da Jetson (jetson/client.py), ela chega aqui, e a
resposta digitada pelo operador do PC volta para o terminal da Jetson —
como um aplicativo simples de troca de mensagens entre dois terminais.

Fluxo por mensagem recebida da Jetson:
  1. recebe uma mensagem do tipo TEXTO;
  2. chama pc.cerebro.responder() — por ora um OPERADOR HUMANO no PC digita
     a resposta, fazendo as vezes do "outro usuário" da conversa;
  3. devolve a resposta como TEXTO para a Jetson.

O máximo do trabalho acontece aqui, no PC: a Jetson só manda e recebe texto
(ver jetson/client.py). Isso é proposital — é a mesma divisão de papéis que
o pipeline de áudio vai usar (Jetson = "ouvido e boca", PC = "cérebro"), só
que sem gravar/tocar áudio ainda.

Quando a Jetson ganhar microfone, troque este servidor por server_voz.py
(mesmo protocolo, mesma função pc.cerebro.responder() por trás — só muda
AUDIO→transcrição no lugar de TEXTO puro) sem precisar mexer no cliente.
Quando o operador humano virar IA de verdade, troque só pc/cerebro.py — ver
docs/roadmap-ia-conversacional.md.

Uso (a partir de src/):
    python -m pc.server_chat [host] [porta]
    (default: 127.0.0.1 5000)
"""

from __future__ import annotations

import socket
import sys

from common.protocol import TEXTO, recv_msg, send_texto
from pc.cerebro import responder


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
        if msg.tipo != TEXTO:
            print(f"[servidor] mensagem ignorada (esperava TEXTO, veio tipo {msg.tipo})")
            continue

        send_texto(conexao, responder(msg.texto))


def main() -> None:
    host = sys.argv[1] if len(sys.argv) > 1 else "127.0.0.1"
    porta = int(sys.argv[2]) if len(sys.argv) > 2 else 5000

    servidor = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    servidor.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    servidor.bind((host, porta))
    servidor.listen(1)
    print(f"[servidor] escutando em {host}:{porta} (Ctrl-C para sair)")
    print("[servidor] modo chat: espera voce digitar a resposta de cada mensagem")

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
