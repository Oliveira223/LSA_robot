"""
chat_client.py — orquestra o dialogo por voz da Jetson com o PC (server_voz.py).

Conecta uma vez no PC e mantem DUAS threads independentes na mesma conexao:
  - envio: espera uma frase inteira do OuvinteVAD (jetson/mic_vad.py) e manda
    pelo protocolo (common/protocol.py), sem esperar resposta da frase
    anterior — o mic continua sendo escutado e a proxima frase e mandada
    assim que estiver pronta, mesmo com uma resposta ainda pendente;
  - recebimento: le a transcricao (bolha "usuario") e o texto da resposta
    (bolha "robo") de cada frase, na ordem em que o servidor manda (o TCP
    garante essa ordem, e o server_voz.py so processa uma frase de cada vez,
    entao as respostas sempre chegam pareadas com o que foi mandado), e fala
    a resposta na hora com o Piper (jetson/tts_server.falar()).

Por que duas threads: um socket TCP e full-duplex (dá pra mandar e receber
ao mesmo tempo sem conflito) — sem isso, o cliente ficava preso esperando a
resposta (transcricao + resposta do operador + falar) antes de sequer
capturar a proxima frase, entao falar varias frases em sequencia rapida
enquanto o operador ainda esta digitando a primeira resposta nao funcionava
(visto na pratica 2026-09-14).

Mesmo padrao de OuvinteVAD (thread daemon + lock + getter) pra
Camera_Simples.py poder desenhar a conversa na tela sem bloquear o video.

Compatibilidade: Python 3.6 da Jetson (sem "from __future__ import
annotations", sem "X | None").

Uso (a partir de src/):
    python3 -m jetson.chat_client [host] [porta]   # teste isolado, so imprime a conversa
    (default: 127.0.0.1 5000)
"""

import socket
import threading
import time

from common.protocol import recv_msg, send_audio
from jetson.mic_vad import ErroDeMic, OuvinteVAD
from jetson.tts_server import falar

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
        self._reconexao_lock = threading.Lock()
        self._rodando = True
        self._thread_envio = threading.Thread(target=self._loop_envio, daemon=True)
        self._thread_recebimento = threading.Thread(target=self._loop_recebimento, daemon=True)
        self._thread_envio.start()
        self._thread_recebimento.start()

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

    def _loop_envio(self):
        while self._rodando:
            wav = self._ouvinte.proxima_fala(timeout=1.0)
            if wav is None:
                continue
            sock = self._sock
            try:
                send_audio(sock, wav)
            except (OSError, ValueError) as e:
                if not self._rodando:
                    break
                print("[chat_client] erro ao enviar audio: %s" % e, flush=True)
                self._reconectar_com_backoff(sock)

    def _loop_recebimento(self):
        while self._rodando:
            sock = self._sock
            try:
                msg_transcricao = recv_msg(sock)
                if msg_transcricao is None:
                    raise ConnectionError("servidor fechou a conexao")
                self._adicionar("usuario", msg_transcricao.texto)

                msg_resposta = recv_msg(sock)
                if msg_resposta is None:
                    raise ConnectionError("servidor fechou a conexao")
                self._adicionar("robo", msg_resposta.texto)
                falar(msg_resposta.texto)
            except (OSError, ValueError) as e:
                if not self._rodando:
                    break
                print("[chat_client] erro na conexao: %s" % e, flush=True)
                self._reconectar_com_backoff(sock)

    def _reconectar_com_backoff(self, sock_com_erro):
        with self._reconexao_lock:
            if sock_com_erro is not self._sock:
                return  # a outra thread (envio ou recebimento) ja reconectou
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
        self._thread_envio.join(timeout=2)
        self._thread_recebimento.join(timeout=2)


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
