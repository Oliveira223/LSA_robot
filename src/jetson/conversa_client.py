"""
conversa_client.py — liga a Jetson 2 (microfone/camera/alto-falante) ao
"cerebro" na Jetson 1 (conversa + voz), pelo cabo de rede entre as duas.

Fluxo por pergunta:
  Jetson 2 --TEXTO (pergunta transcrita)--------------------------> Jetson 1
  Jetson 2 <--TEXTO (frase 1), AUDIO (wav da frase 1)------------- Jetson 1
  Jetson 2 <--TEXTO (frase 2), AUDIO (wav da frase 2)------------- Jetson 1
  ...
  Jetson 2 <--TEXTO vazio ("") = fim da resposta------------------ Jetson 1

A Jetson 1 manda cada frase assim que ela fica pronta (a IA gera frase por
frase e o Piper sintetiza cada uma), entao o robo comeca a falar antes da
resposta inteira terminar. Mesmo protocolo de common/protocol.py.

Aqui na Jetson 2:
  - cada frase vira uma bolha ("robo", texto) na janela de mensagens;
  - o WAV da frase e enfileirado e tocado em ordem no alto-falante (HDMI);
  - `falando()` fica True enquanto o robo fala (e um pouco depois), pra o
    microfone da PrimeSense nao transcrever a voz do proprio robo;
  - se a Jetson 1 cair ou nao estiver ligada, reconecta sozinho em segundo
    plano — a camera e a transcricao continuam funcionando.

Compatibilidade: Python 3.6 da Jetson (sem "from __future__ import
annotations", sem "X | None").

Uso (a partir de src/, teste isolado; digite e a resposta e falada):
    python3 -m jetson.conversa_client [host] [porta]
"""

import io
import os
import queue
import random
import socket
import struct
import subprocess
import tempfile
import threading
import time
import wave

from common.protocol import AUDIO, TEXTO, recv_msg, send_texto

CTRL = "\x00"                          # TEXTO de controle do servidor (ver servidor_conversa.py)
PREFIXO_FALAR = CTRL + "falar:"
TIMEOUT_FALAR_S = 20.0
TIMEOUT_RESPOSTA_S = 90.0               # depois disso `aguardando()` desiste

# O monitor HDMI "dorme" o audio entre as falas e corta a 1a silaba quando o som volta
# (ver jetson/bin/say e say-keepalive, que resolvem igual): um fluxo continuo de
# silencio (keepalive) mais um ruido inaudivel colado antes de cada fala.
KEEPALIVE_BIN = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bin", "say-keepalive")
LEAD_MS = 50
LEAD_AMP = 30                            # ~ -60 dBFS: inaudivel, mas acorda o amplificador

HOST_PADRAO = "10.10.10.1"
PORTA_PADRAO = 5005

# saida do alto-falante do robo (HDMI da Jetson 2, mesmo sink do jetson/bin/say)
SINK_PADRAO = "alsa_output.platform-3510000.hda.hdmi-stereo-extra1"

RECONEXAO_BACKOFF_S = 2.0
RECONEXAO_BACKOFF_MAX_S = 10.0
TIMEOUT_CONEXAO_S = 3.0
CAUDA_S = 0.7            # o microfone continua mudo por isso apos o fim da fala
MAX_MENSAGENS = 8


def com_lead_in(wav, ms=LEAD_MS, amp=LEAD_AMP):
    """Devolve o WAV com `ms` de ruido quase inaudivel na frente (acorda o audio HDMI
    antes da 1a silaba). Se o WAV nao for PCM 16 bits, devolve como veio."""
    try:
        with wave.open(io.BytesIO(wav), "rb") as w:
            canais, largura, taxa = w.getnchannels(), w.getsampwidth(), w.getframerate()
            quadros = w.readframes(w.getnframes())
        if largura != 2:
            return wav
        n = int(taxa * ms / 1000) * canais
        ruido = struct.pack("<%dh" % n, *[random.randint(-amp, amp) for _ in range(n)])
        saida = io.BytesIO()
        with wave.open(saida, "wb") as w:
            w.setnchannels(canais)
            w.setsampwidth(largura)
            w.setframerate(taxa)
            w.writeframes(ruido + quadros)
        return saida.getvalue()
    except (wave.Error, EOFError, struct.error):
        return wav


def _ligar_keepalive():
    """Mantem um fluxo de silencio no sink HDMI (idempotente, em segundo plano)."""
    if not os.path.exists(KEEPALIVE_BIN):
        return
    try:
        subprocess.run([KEEPALIVE_BIN, "start"], timeout=20,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError) as e:
        print("[conversa] keepalive de audio nao subiu: %s" % e, flush=True)


class ClienteConversa:
    """
    enviar(texto)     manda uma pergunta ao cerebro (nao bloqueia)
    mensagens()       bolhas ("robo", frase) recebidas; pareia com as do usuario
                      na ordem em que chegam (o transcritor guarda as dele)
    conectado()       True se ha conexao com a Jetson 1 agora
    falando()         True enquanto o robo fala (+ CAUDA_S)
    """

    def __init__(self, host=HOST_PADRAO, porta=PORTA_PADRAO, sink=SINK_PADRAO,
                 ao_mensagem=None):
        self._host, self._porta, self._sink = host, porta, sink
        self._ao_mensagem = ao_mensagem      # funcao(autor, texto) a cada bolha nova
        self._lock = threading.Lock()
        self._sock = None
        self._conectado = False
        self._rodando = True
        self._tocando = 0                    # frases na fila de reproducao ou tocando
        self._mudo_ate = 0.0
        self._suporta_falar = False          # servidor novo anuncia no hello
        self._fim = threading.Event()        # setado quando chega o TEXTO vazio (fim da resposta)
        self._aguardando_desde = 0.0         # !=0 enquanto espera o fim de uma resposta
        self._descartar = False              # `interromper`: ignora o audio ate o fim da resposta
        self._tocador = None                 # paplay em andamento (pra poder interromper)
        self._envio = queue.Queue()
        self._reproducao = queue.Queue()
        threading.Thread(target=_ligar_keepalive, daemon=True).start()
        threading.Thread(target=self._loop_conexao, daemon=True).start()
        threading.Thread(target=self._loop_envio, daemon=True).start()
        threading.Thread(target=self._loop_reproducao, daemon=True).start()

    # ── consulta ───────────────────────────────────────────────────────
    def conectado(self):
        return self._conectado

    def falando(self):
        return self._tocando > 0 or time.time() < self._mudo_ate

    def aguardando(self):
        """True desde que uma pergunta foi enviada ate chegar o fim da resposta."""
        desde = self._aguardando_desde
        return bool(desde) and time.time() - desde < TIMEOUT_RESPOSTA_S

    def interromper(self):
        """Corta a fala agora: para o audio que esta tocando, esvazia a fila e ignora o
        resto do audio da resposta em curso (o texto continua chegando). Devolve quantas
        frases foram descartadas."""
        descartadas = 0
        with self._lock:
            if self.aguardando():        # so ha o que descartar se a resposta ainda esta chegando
                self._descartar = True
            while True:
                try:
                    self._reproducao.get_nowait()
                except queue.Empty:
                    break
                self._tocando -= 1
                descartadas += 1
            tocador = self._tocador
        if tocador is not None and tocador.poll() is None:
            tocador.terminate()
            descartadas += 1
        return descartadas

    def descartando(self):
        """True se o audio do resto da resposta em curso esta sendo ignorado (apos interromper)."""
        return self._descartar

    def suporta_falar(self):
        return self._conectado and self._suporta_falar

    def falar_literal(self, texto, timeout=TIMEOUT_FALAR_S):
        """Pede a Jetson 1 pra falar `texto` com a voz do robo (Piper), sem passar
        pelo cerebro, e espera terminar de tocar aqui. Devolve (ok, mensagem)."""
        if not self._conectado:
            return False, "Jetson 1 desconectada"
        if not self._suporta_falar:
            return False, ("o servidor da Jetson 1 e antigo e nao sabe falar texto literal "
                           "(atualize servidor_conversa.py la e reinicie)")
        self._fim.clear()
        self._aguardando_desde = time.time()
        self._envio.put(PREFIXO_FALAR + texto)
        if not self._fim.wait(timeout):
            self._descartar = True       # se a fala chegar atrasada, nao toca por cima de outra voz
            return False, "a Jetson 1 nao respondeu em %.0f s" % timeout
        limite = time.time() + 60.0
        while self.falando() and time.time() < limite:
            time.sleep(0.1)
        return True, "voz do robo (Piper, Jetson 1)"

    def enviar(self, texto):
        """Enfileira a pergunta. Se nao ha conexao, avisa e descarta (o robo
        nao vai responder uma pergunta de minutos atras quando voltar)."""
        if not self._conectado:
            self._nova_bolha("robo", "Estou sem conexão com o meu cérebro.")
            return
        self._aguardando_desde = time.time()
        self._envio.put(texto)

    def _nova_bolha(self, autor, texto):
        if self._ao_mensagem:
            self._ao_mensagem(autor, texto)

    # ── conexao / recebimento ──────────────────────────────────────────
    def _loop_conexao(self):
        espera = RECONEXAO_BACKOFF_S
        while self._rodando:
            try:
                s = socket.create_connection((self._host, self._porta), timeout=TIMEOUT_CONEXAO_S)
                s.settimeout(None)
                s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            except OSError:
                time.sleep(espera)
                espera = min(espera * 2, RECONEXAO_BACKOFF_MAX_S)
                continue
            espera = RECONEXAO_BACKOFF_S
            with self._lock:
                self._sock = s
            self._suporta_falar = False
            self._aguardando_desde = 0.0
            self._conectado = True
            print("[conversa] conectado em %s:%d" % (self._host, self._porta), flush=True)
            try:
                self._receber(s)
            except (OSError, ValueError) as e:
                if self._rodando:
                    print("[conversa] conexao caiu: %s" % e, flush=True)
            finally:
                self._conectado = False
                with self._lock:
                    self._sock = None
                try:
                    s.close()
                except OSError:
                    pass

    def _receber(self, s):
        while self._rodando:
            msg = recv_msg(s)
            if msg is None:
                raise ConnectionError("Jetson 1 fechou a conexao")
            if msg.tipo == TEXTO:
                texto = msg.texto.strip()
                if texto.startswith(CTRL):         # controle do servidor, nao e fala
                    if "falar" in texto:
                        self._suporta_falar = True
                        print("[conversa] servidor sabe falar texto literal", flush=True)
                elif texto:
                    self._nova_bolha("robo", texto)
                else:                  # texto vazio = fim da resposta
                    self._aguardando_desde = 0.0
                    self._descartar = False
                    self._fim.set()
            elif msg.tipo == AUDIO and msg.dados:
                if self._descartar:
                    print("[conversa] audio descartado (interrompido)", flush=True)
                    continue
                print("[conversa] audio recebido (%d bytes)" % len(msg.dados), flush=True)
                with self._lock:
                    self._tocando += 1
                self._reproducao.put(msg.dados)

    # ── envio ──────────────────────────────────────────────────────────
    def _loop_envio(self):
        while self._rodando:
            try:
                texto = self._envio.get(timeout=1.0)
            except queue.Empty:
                continue
            with self._lock:
                s = self._sock
            if s is None:
                continue
            try:
                send_texto(s, texto)
            except (OSError, ValueError) as e:
                print("[conversa] erro ao enviar: %s" % e, flush=True)

    # ── reproducao ─────────────────────────────────────────────────────
    def _loop_reproducao(self):
        while self._rodando:
            try:
                wav = self._reproducao.get(timeout=1.0)
            except queue.Empty:
                continue
            caminho = None
            try:
                with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
                    f.write(com_lead_in(wav))
                    caminho = f.name
                for tentativa in (1, 2):
                    tocador = subprocess.Popen(["paplay", "-d", self._sink, caminho],
                                               stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
                    self._tocador = tocador
                    try:
                        _, erro = tocador.communicate(timeout=60)
                    except subprocess.TimeoutExpired:
                        tocador.kill()
                        _, erro = tocador.communicate()
                    if tocador.returncode == 0 or tocador.returncode < 0:   # <0: interrompido de proposito
                        break
                    print("[conversa] paplay rc=%d: %s" % (tocador.returncode, erro.decode(errors="replace").strip()), flush=True)
                    if tentativa == 1:     # PulseAudio pode ter caido: religa e tenta de novo
                        subprocess.run(["pulseaudio", "--start"], timeout=20,
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except (OSError, subprocess.SubprocessError) as e:
                print("[conversa] falha ao tocar: %s" % e, flush=True)
            finally:
                self._tocador = None
                if caminho:
                    try:
                        os.remove(caminho)
                    except OSError:
                        pass
                with self._lock:
                    self._tocando -= 1
                    self._mudo_ate = time.time() + CAUDA_S

    def parar(self):
        self._rodando = False
        with self._lock:
            s = self._sock
        if s is not None:
            try:
                s.close()
            except OSError:
                pass


if __name__ == "__main__":
    import sys

    host = sys.argv[1] if len(sys.argv) > 1 else HOST_PADRAO
    porta = int(sys.argv[2]) if len(sys.argv) > 2 else PORTA_PADRAO
    cliente = ClienteConversa(host, porta, ao_mensagem=lambda a, t: print("[%s] %s" % (a, t), flush=True))
    try:
        while True:
            pergunta = input("voce> ").strip()
            if pergunta:
                cliente.enviar(pergunta)
    except (KeyboardInterrupt, EOFError):
        pass
    finally:
        cliente.parar()
