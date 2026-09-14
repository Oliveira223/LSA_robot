"""
chat_client.py — orquestra o dialogo por voz da Jetson com o PC (server_voz.py).

Conecta uma vez no PC e, em loop continuo: espera uma frase inteira do
OuvinteVAD (jetson/mic_vad.py), manda pelo protocolo (common/protocol.py) e
recebe de volta a transcricao (vira bolha "usuario"), o texto da resposta
(vira bolha "robo") e, se o TTS estiver disponivel no PC, o audio
sintetizado — que toca direto no speaker HDMI da Jetson (mesmo sink pinado
que ja usamos pro say/spotifyd; nao depende do sink padrao do PulseAudio,
que ja vimos resetar sozinho nessa Jetson).

Mesmo padrao de OuvinteVAD (thread daemon + lock + getter) pra
Camera_Simples.py poder desenhar a conversa na tela sem bloquear o video.

Compatibilidade: Python 3.6 da Jetson (sem "from __future__ import
annotations", sem "X | None").

Uso (a partir de src/):
    python3 -m jetson.chat_client [host] [porta]   # teste isolado, so imprime a conversa
    (default: 127.0.0.1 5000)
"""

import socket
import subprocess
import threading
import time

from common.protocol import AUDIO, recv_msg, send_audio
from jetson.mic_vad import ErroDeMic, OuvinteVAD

SINK_HDMI = "alsa_output.platform-3510000.hda.hdmi-stereo-extra1"
MAX_MENSAGENS = 6                  # quantas bolhas manter (as mais recentes)
RECONEXAO_BACKOFF_S = 2.0
RECONEXAO_BACKOFF_MAX_S = 30.0


class ErroDeChat(Exception):
    """Falha previsivel ao montar o cliente de chat (mic ou conexao)."""


class ClienteChat:
    """
    Escuta o mic continuamente e troca mensagens com o PC.

    mensagens() devolve as ultimas MAX_MENSAGENS tuplas (autor, texto) —
    autor e "usuario" ou "robo" — protegidas por lock.
    """

    def __init__(self, host, porta, ouvinte=None):
        self._host = host
        self._porta = porta
        self._ouvinte_proprio = ouvinte is None

        try:
            self._ouvinte = ouvinte or OuvinteVAD()
        except ErroDeMic as e:
            raise ErroDeChat("microfone indisponivel: %s" % e) from e

        try:
            self._sock = self._conectar()
        except OSError as e:
            if self._ouvinte_proprio:
                self._ouvinte.parar()
            raise ErroDeChat(
                "nao foi possivel conectar em %s:%d (%s)" % (host, porta, e)) from e

        self._lock = threading.Lock()
        self._mensagens = []
        self._rodando = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def _conectar(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(5)
        s.connect((self._host, self._porta))
        s.settimeout(None)
        return s

    def mensagens(self):
        with self._lock:
            return list(self._mensagens)

    def _adicionar(self, autor, texto):
        with self._lock:
            self._mensagens.append((autor, texto))
            if len(self._mensagens) > MAX_MENSAGENS:
                self._mensagens.pop(0)

    def _loop(self):
        while self._rodando:
            wav = self._ouvinte.proxima_fala(timeout=1.0)
            if wav is None:
                continue

            try:
                send_audio(self._sock, wav)

                msg_transcricao = recv_msg(self._sock)
                if msg_transcricao is None:
                    raise ConnectionError("servidor fechou a conexao")
                self._adicionar("usuario", msg_transcricao.texto)

                msg_resposta = recv_msg(self._sock)
                if msg_resposta is None:
                    raise ConnectionError("servidor fechou a conexao")
                self._adicionar("robo", msg_resposta.texto)

                msg_audio = recv_msg(self._sock)
                if msg_audio is not None and msg_audio.tipo == AUDIO:
                    self._tocar(msg_audio.dados)
            except (OSError, ValueError) as e:
                if not self._rodando:
                    break
                print("[chat_client] erro na conexao: %s" % e, flush=True)
                self._reconectar_com_backoff()

    def _tocar(self, wav_bytes):
        try:
            proc = subprocess.Popen(
                ["paplay", "--device=%s" % SINK_HDMI],
                stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            proc.communicate(wav_bytes, timeout=30)
        except Exception as e:
            print("[chat_client] falha ao tocar resposta: %s" % e, flush=True)

    def _reconectar_com_backoff(self):
        try:
            self._sock.close()
        except Exception:
            pass
        espera = RECONEXAO_BACKOFF_S
        while self._rodando:
            print("[chat_client] tentando reconectar em %.0fs..." % espera, flush=True)
            time.sleep(espera)
            if not self._rodando:
                return
            try:
                self._sock = self._conectar()
                print("[chat_client] reconectado", flush=True)
                return
            except OSError:
                espera = min(espera * 2, RECONEXAO_BACKOFF_MAX_S)

    def parar(self):
        self._rodando = False
        if self._ouvinte_proprio:
            self._ouvinte.parar()
        try:
            self._sock.close()
        except Exception:
            pass
        self._thread.join(timeout=2)


if __name__ == "__main__":
    # Teste isolado: conecta de verdade no servidor e imprime a conversa,
    # sem GUI. Precisa de um pc.server_voz alcancavel na rede.
    import sys

    host = sys.argv[1] if len(sys.argv) > 1 else "127.0.0.1"
    porta = int(sys.argv[2]) if len(sys.argv) > 2 else 5000

    print("[chat_client] conectando em %s:%d..." % (host, porta), flush=True)
    try:
        cliente = ClienteChat(host, porta)
    except ErroDeChat as e:
        print("[chat_client] %s" % e, flush=True)
        raise SystemExit(1)

    print("[chat_client] conectado — fale perto do mic (Ctrl-C sai)", flush=True)
    vistas = 0
    try:
        while True:
            msgs = cliente.mensagens()
            for autor, texto in msgs[vistas:]:
                print("[%s] %s" % (autor, texto), flush=True)
            vistas = len(msgs)
            time.sleep(0.2)
    except KeyboardInterrupt:
        pass
    finally:
        cliente.parar()
        print("\n[chat_client] encerrado", flush=True)
