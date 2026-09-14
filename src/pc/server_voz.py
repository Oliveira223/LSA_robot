"""
server_voz.py — servidor da etapa (d2): áudio → texto → resposta em voz.

Fluxo por mensagem recebida da Jetson:
  1. recebe um WAV (mensagem do tipo AUDIO);
  2. transcreve com faster-whisper (stt.transcrever);
  3. mostra a transcrição no terminal;
  4. chama pc.cerebro.responder() — por ora um OPERADOR HUMANO digita a
     resposta, simulando a IA;
  5. sintetiza a resposta com espeak-ng (pc.tts) e devolve como mensagem
     AUDIO para a Jetson, que só toca no speaker. Se o TTS não estiver
     disponível, cai para enviar a resposta como texto puro.

O passo 4 é o ponto que, mais adiante, vira uma chamada de IA de verdade
(regras + modelo) — trocar pc/cerebro.py não exige mexer neste arquivo.
A síntese de voz roda aqui no PC de propósito: a Jetson não tem motor de
TTS (ver docs/roadmap-comunicacao.md, Fase 4).

Uso (a partir de src/):
    python -m pc.server_voz [host] [porta]
    (default: 127.0.0.1 5000)
"""

from __future__ import annotations

import os
import socket
import sys
import tempfile

from common.protocol import AUDIO, recv_msg, send_audio, send_texto
from pc import stt, tts
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

        resposta = responder(texto)
        print(f"[servidor] resposta: {resposta!r}")
        try:
            wav = tts.sintetizar(resposta)
        except tts.ErroDeTTS as e:
            print(f"[servidor] TTS indisponivel ({e}); enviando resposta como texto")
            send_texto(conexao, resposta)
            continue
        send_audio(conexao, wav)
        print(f"[servidor] resposta enviada em voz: {len(wav)} bytes", flush=True)


def main() -> None:
    host = sys.argv[1] if len(sys.argv) > 1 else "127.0.0.1"
    porta = int(sys.argv[2]) if len(sys.argv) > 2 else 5000

    servidor = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    servidor.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    servidor.bind((host, porta))
    servidor.listen(1)
    print(f"[servidor] escutando em {host}:{porta} (Ctrl-C para sair)")
    print("[servidor] modo voz: transcreve o audio, voce digita a resposta, ela volta em voz")
    if not tts.disponivel():
        print("[servidor] AVISO: espeak-ng nao encontrado — a resposta voltara como texto.")
        print("[servidor]        instale com: sudo apt install -y espeak-ng")

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
