"""
tts_server.py — servidor de voz da Jetson (arquitetura invertida, "por
enquanto"): a fala e capturada no mic do PC (pc/voice_client.py), o PC
transcreve (pc/stt.py) e decide a resposta (pc/cerebro.py, um operador
humano digitando por ora); esse texto chega aqui por TCP e a Jetson fala
com o Piper (bin/say) no proprio speaker HDMI.

    [Mic no PC] -> pc.stt -> pc.cerebro -> texto -> rede
        -> [Jetson: tts_server.py] -> bin/say -> Speaker HDMI da Jetson

Por que essa troca (ver conversa que motivou isso): o mic da Jetson
(PrimeSense, via jetson/mic_vad.py) disputa a mesma interface USB com a
camera, e o TTS local da Jetson (Piper) e bem melhor que o espeak-ng do
PC (pc/tts.py) — entao, por enquanto, o mic fica no PC e a voz de saida
fica na Jetson. cerebro.py continua no PC; este servidor so recebe o
texto ja pronto e manda falar.

Compatibilidade: Python 3.6 da Jetson (sem "from __future__ import
annotations", sem "X | None") — mesma regra de jetson/mic_vad.py e
jetson/chat_client.py.

Uso (a partir de src/, na Jetson):
    python3 -m jetson.tts_server [host] [porta]
    (default: 0.0.0.0 5001 — porta diferente da 5000 do pc.server_voz,
    pra poder rodar os dois ao mesmo tempo se precisar)
"""

import os
import subprocess
import sys

from common.protocol import TEXTO, recv_msg, send_texto
from common.tcp_server import servir

SAY_BIN = os.path.expanduser("~/dev/LSA_robot/src/jetson/bin/say")


def falar(texto):
    """Chama o script `say` (Piper) em modo pontual (-1): fala e sai."""
    texto = (texto or "").strip()
    if not texto:
        return
    try:
        subprocess.run([SAY_BIN, "-1", texto])
    except OSError as e:
        print("[tts_server] falha ao chamar '%s': %s" % (SAY_BIN, e))


def atender(conexao):
    while True:
        try:
            msg = recv_msg(conexao)
        except (ConnectionResetError, ValueError) as e:
            print("[tts_server] erro na conexao: %s" % e)
            return
        if msg is None:
            print("[tts_server] cliente desconectou")
            return
        if msg.tipo != TEXTO:
            print("[tts_server] mensagem ignorada (esperava TEXTO, veio tipo %s)" % msg.tipo)
            continue

        texto = msg.texto
        print("[tts_server] falando: %r" % texto)
        falar(texto)
        send_texto(conexao, "ok")


def main():
    host = sys.argv[1] if len(sys.argv) > 1 else "0.0.0.0"
    porta = int(sys.argv[2]) if len(sys.argv) > 2 else 5001

    if not os.path.exists(SAY_BIN):
        print("[tts_server] AVISO: '%s' nao encontrado — a fala vai falhar." % SAY_BIN)

    print("[tts_server] fala com: %s -1 <texto>" % SAY_BIN)
    servir(host, porta, atender, prefixo="[tts_server]")


if __name__ == "__main__":
    main()
