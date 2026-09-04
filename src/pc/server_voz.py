"""
server_voz.py — servidor da etapa (d): áudio → texto → resposta.

Ainda não usado em produção: a Jetson atual não tem microfone. Fica pronto
para quando o hardware chegar — até lá, pc/server_chat.py cobre a troca de
mensagens usando só texto.

Fluxo por mensagem recebida da Jetson:
  1. recebe um WAV (mensagem do tipo AUDIO);
  2. transcreve com faster-whisper (stt.transcrever);
  3. mostra a transcrição no terminal;
  4. chama pc.cerebro.responder() — por ora um OPERADOR HUMANO digita a
     resposta, simulando a IA;
  5. devolve a resposta como texto para a Jetson.

O passo 4 é o ponto que, mais adiante, vira uma chamada de IA de verdade
(regras + modelo) — trocar pc/cerebro.py não exige mexer neste arquivo.

Uso (a partir de src/):
    python -m pc.server_voz [host] [porta]
    (default: 127.0.0.1 5000)
"""

from __future__ import annotations

import os
import socket
import sys
import tempfile

from common.protocol import AUDIO, recv_msg, send_texto
from pc import stt
from pc.cerebro import responder


def _transcrever_bytes(wav_bytes: bytes) -> str:
    """Grava os bytes num arquivo temporário só para o faster-whisper ler."""
    tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    try:
        tmp.write(wav_bytes)
        tmp.close()
        return stt.transcrever(tmp.name)
    finally:
        try:
            os.remove(tmp.name)
        except OSError:
            pass


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
        if msg.tipo != AUDIO:
            print(f"[servidor] mensagem ignorada (esperava AUDIO, veio tipo {msg.tipo})")
            continue

        print(f"[servidor] audio recebido: {len(msg.dados)} bytes", flush=True)
        try:
            texto = _transcrever_bytes(msg.dados)
        except stt.ErroDeSTT as e:
            print(f"[servidor] erro de transcricao: {e}")
            send_texto(conexao, "(desculpe, nao consegui entender o audio)")
            continue

        if not texto:
            print("[servidor] nada foi transcrito — o audio pode estar sem fala")
        send_texto(conexao, responder(texto))


def main() -> None:
    host = sys.argv[1] if len(sys.argv) > 1 else "127.0.0.1"
    porta = int(sys.argv[2]) if len(sys.argv) > 2 else 5000

    servidor = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    servidor.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    servidor.bind((host, porta))
    servidor.listen(1)
    print(f"[servidor] escutando em {host}:{porta} (Ctrl-C para sair)")
    print("[servidor] modo voz: transcreve o audio e espera voce digitar a resposta")

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
