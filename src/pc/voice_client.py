"""
voice_client.py — cliente de voz do PC (arquitetura invertida, "por
enquanto"): fala no mic do PC, este processo transcreve (pc/stt.py) e
chama pc/cerebro.py pra voce digitar a resposta — exatamente como
pc/push_to_talk.py ja faz localmente. A unica diferenca e que a resposta,
em vez de so aparecer no terminal, e mandada pela rede pra
jetson/tts_server.py falar la (Piper, speaker HDMI da Jetson).

    [Mic no PC] -> stt.transcrever_array -> cerebro.responder -> texto
        -> rede -> [Jetson: tts_server.py fala]

Uso (a partir de src/):
    python3 -m pc.voice_client [host_da_jetson] [porta] [indice_do_microfone]
    (default: 127.0.0.1 5001 — troque pelo IP da Jetson na rede,
    ex.: python3 -m pc.voice_client 192.168.0.103)
"""

from __future__ import annotations

import socket
import sys

from common.audio_io import Cronometro, ErroDeAudioIO, Gravador
from common.cli import ler as _ler, sair as _sair
from common.protocol import recv_msg, send_texto
from pc import stt
from pc.cerebro import responder
from pc.push_to_talk import preparar_audio


def _uma_rodada(sock: socket.socket, grav: Gravador) -> bool:
    """Uma gravação + transcrição + resposta + envio. Devolve False pra sair."""
    if _sair(_ler("▶ falar > ")):
        return False

    grav.iniciar()
    with Cronometro("● gravando…", "  (ENTER para parar)"):
        parar = _ler("")
    if _sair(parar):
        grav.parar()
        return False
    print()

    audio = grav.parar()
    dur = audio.size / grav.taxa
    if dur < 0.3:
        print("[cliente] muito curto — ignorado\n")
        return True

    tratado, _pico, _ganho = preparar_audio(audio)
    with Cronometro("  transcrevendo…"):
        try:
            texto = stt.transcrever_array(tratado, taxa=grav.taxa)
        except stt.ErroDeSTT as e:
            print(f"\r  erro: {e}            \n")
            return True
    print(f'\r  "{texto}"' if texto else "\r  (nada reconhecido)", " " * 24)

    resposta = responder(texto)
    if not resposta.strip():
        print("[cliente] resposta vazia — nada enviado pra Jetson falar\n")
        return True

    send_texto(sock, resposta)
    if recv_msg(sock) is None:
        print("[cliente] Jetson desconectou")
        return False
    print(f"[cliente] Jetson falando: {resposta!r}\n")
    return True


def main() -> None:
    host = sys.argv[1] if len(sys.argv) > 1 else "127.0.0.1"
    porta = int(sys.argv[2]) if len(sys.argv) > 2 else 5001
    indice_mic = int(sys.argv[3]) if len(sys.argv) > 3 else None

    stt._obter_modelo()  # carrega antes de conectar (a 1a vez baixa o modelo)

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.connect((host, porta))
        print(f"[cliente] conectado na Jetson em {host}:{porta}")

        try:
            grav = Gravador(indice_mic)
        except ErroDeAudioIO as e:
            print(f"[cliente] {e}")
            raise SystemExit(1)

        with grav:
            print(f"[cliente] mic: {grav.nome} · {grav.taxa} Hz · modelo {stt.MODELO}")
            print("[cliente] ENTER grava / ENTER para · digite a resposta do robo · q sai\n")
            try:
                while _uma_rodada(sock, grav):
                    pass
            except KeyboardInterrupt:
                print("\n[cliente] encerrando")


if __name__ == "__main__":
    main()
