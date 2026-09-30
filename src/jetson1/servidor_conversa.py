"""
servidor_conversa.py — cerebro + voz do robo, roda na JETSON 1.

Recebe da Jetson 2 (pelo cabo, TCP) a pergunta ja transcrita, gera a
resposta com o brain.py do repositorio Bitnet_TTS e devolve, frase por frase
e assim que cada uma fica pronta, o texto e o WAV falado (Piper):

    recebe  TEXTO  pergunta
    envia   TEXTO  frase 1     AUDIO  wav da frase 1
    envia   TEXTO  frase 2     AUDIO  wav da frase 2   ...
    envia   TEXTO  ""          (fim da resposta)

Falar texto literal (sem cerebro): ao conectar, o servidor manda um TEXTO de
controle (comeca com "\\x00") dizendo "LSA1 falar". Se o cliente o recebeu, pode
mandar TEXTO "\\x00falar:<texto>" e o servidor so sintetiza esse texto com o
Piper (mesma voz das respostas), sem IA nem memoria, e fecha com TEXTO "".
Servidor antigo nao manda o aviso — o cliente entao nem tenta.

Quem toca o audio e a Jetson 2 (o alto-falante do robo esta la) — ver
src/jetson/conversa_client.py. Este servidor nao usa microfone nem
alto-falante.

Instalacao: copie este arquivo e common/protocol.py (como protocol.py) pra
pasta do Bitnet_TTS na Jetson 1 e rode la:

    python3 servidor_conversa.py                 # 0.0.0.0:5005
    python3 servidor_conversa.py --falso         # sem brain/Piper: teste de conexao

Configuracao por ambiente (caminhos do Piper e do banco, que no repositorio
original apontavam pro PC do autor):
    LSA_PIPER_DIR    pasta do binario do Piper           (~/piper)
    LSA_PIPER_VOICE  modelo .onnx da voz                 (<piper>/voices/pt_BR-faber-medium.onnx)
    LSA_DB           banco do historico de conversas     (<pasta do script>/memoria.db)
    LSA_LLM_THREADS  nucleos pro Ollama                  (todos)
    LSA_LLM_MAX_TOKENS  tamanho maximo da resposta        (60)

Compatibilidade: Python 3.6 da Jetson (sem "from __future__ import annotations").
"""

import argparse
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import threading
import time

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, AQUI)
try:
    from protocol import TEXTO, recv_msg, send_audio, send_texto
except ImportError:                                  # rodando de dentro de src/
    sys.path.insert(0, os.path.join(AQUI, ".."))
    from common.protocol import TEXTO, recv_msg, send_audio, send_texto

PORTA_PADRAO = 5005
CTRL = "\x00"                                       # TEXTO de controle (nunca e fala de verdade)
HELLO = CTRL + "LSA1 falar"
PREFIXO_FALAR = CTRL + "falar:"
FRASE_PENSANDO = "deixa eu pensar..."
FRASE_FINAL = "Até mais! Foi um prazer conversar com você."
_FIM_FRASE = re.compile(r"(?<=[.!?])\s+")


class Piper:
    """Piper residente (--json-input): o modelo carrega uma vez e cada frase
    vira um WAV, sem pagar o custo de iniciar o processo a cada fala."""

    def __init__(self, pasta=None, voz=None):
        pasta = pasta or os.environ.get("LSA_PIPER_DIR") or os.path.expanduser("~/piper")
        voz = voz or os.environ.get("LSA_PIPER_VOICE") or os.path.join(pasta, "voices", "pt_BR-faber-medium.onnx")
        for caminho in (os.path.join(pasta, "piper"), voz):
            if not os.path.exists(caminho):
                raise FileNotFoundError("Piper: nao encontrei %s" % caminho)
        self._saida = tempfile.mkdtemp(prefix="lsa-piper-")
        self._lock = threading.Lock()
        self._proc = subprocess.Popen(
            [os.path.join(pasta, "piper"), "-m", voz, "--json-input", "--output_dir", self._saida],
            cwd=pasta, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            universal_newlines=True, bufsize=1)

    def sintetizar(self, texto):
        """Devolve os bytes do WAV falado."""
        with self._lock:
            self._proc.stdin.write(json.dumps({"text": texto}, ensure_ascii=False) + "\n")
            self._proc.stdin.flush()
            caminho = self._proc.stdout.readline().strip()
        if not caminho or not os.path.exists(caminho):
            raise RuntimeError("Piper nao gerou audio para %r" % texto)
        try:
            with open(caminho, "rb") as f:
                return f.read()
        finally:
            os.remove(caminho)


class Cerebro:
    """Envolve o brain.py + memoria.py do Bitnet_TTS."""

    def __init__(self):
        sys.path.insert(0, AQUI)
        import brain
        import memoria
        memoria.DB_PATH = os.environ.get("LSA_DB") or os.path.join(AQUI, "memoria.db")
        memoria.criar_banco()
        self.brain, self.memoria = brain, memoria
        # o brain.py usa metade dos nucleos (pensado pra um i7); a TX2 so tem CPU
        # pro modelo e gera ~4,4 tok/s com os 4 nucleos contra ~3,9 com 2.
        brain.OLLAMA_OPTIONS["num_thread"] = int(os.environ.get("LSA_LLM_THREADS") or os.cpu_count() or 4)
        brain.OLLAMA_OPTIONS["num_predict"] = int(os.environ.get("LSA_LLM_MAX_TOKENS") or 60)
        brain.aquecer_ollama_em_thread()

    def responder(self, pergunta, ao_gerar_frase):
        """Chama ao_gerar_frase(frase) a cada frase pronta; devolve a resposta inteira."""
        b = self.brain
        texto_corrigido = b.corrigir_texto(pergunta)
        if b.e_despedida(texto_corrigido):
            ao_gerar_frase(FRASE_FINAL)
            self.memoria.salvar_conversa(pergunta, FRASE_FINAL)
            return FRASE_FINAL

        faladas = []

        def frase(f):
            faladas.append(f)
            ao_gerar_frase(f)

        resposta = b.gerar_resposta(pergunta, ao_gerar_frase=frase,
                                    ao_pensar=lambda: frase(FRASE_PENSANDO))
        if resposta and not [f for f in faladas if f != FRASE_PENSANDO]:
            # resposta local (frases fixas, conta, geografia): vem inteira, entao
            # separa em frases pra a primeira ja comecar a falar
            for parte in _FIM_FRASE.split(resposta.strip()):
                if parte.strip():
                    frase(parte.strip())
        self.memoria.salvar_conversa(pergunta, resposta)
        if resposta in b.FRASES_NAO_ENTENDI:
            self.memoria.registrar_nao_respondida(pergunta, texto_corrigido)
        return resposta


class CerebroFalso:
    """So pra testar a conexao: repete a pergunta, sem IA nem voz."""

    def responder(self, pergunta, ao_gerar_frase):
        ao_gerar_frase("Você disse: %s." % pergunta)
        return pergunta


def atender(conn, cerebro, tts):
    def enviar_frase(frase):
        send_texto(conn, frase)
        if tts is not None:
            try:
                send_audio(conn, tts.sintetizar(frase))
            except Exception as e:                   # sem voz, o texto ainda aparece
                print("[servidor] TTS falhou: %s" % e, flush=True)

    send_texto(conn, HELLO)                           # avisa que sabe falar texto literal
    while True:
        msg = recv_msg(conn)
        if msg is None:
            print("[servidor] cliente desconectou", flush=True)
            return
        if msg.tipo != TEXTO:
            continue
        pergunta = msg.texto.strip()
        if not pergunta:
            continue
        if pergunta.startswith(PREFIXO_FALAR):
            literal = pergunta[len(PREFIXO_FALAR):].strip()
            print("[servidor] falar (literal): %r" % literal, flush=True)
            if literal:
                enviar_frase(literal)
            send_texto(conn, "")                      # fim
            continue
        t0 = time.time()
        print("[servidor] pergunta: %r" % pergunta, flush=True)
        try:
            cerebro.responder(pergunta, enviar_frase)
        except (OSError, ValueError):
            raise
        except Exception as e:
            print("[servidor] erro no cerebro: %r" % (e,), flush=True)
            enviar_frase("Desculpe, tive um problema para pensar nisso.")
        send_texto(conn, "")                          # fim da resposta
        print("[servidor] resposta enviada em %.1fs" % (time.time() - t0), flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--porta", type=int, default=PORTA_PADRAO)
    ap.add_argument("--falso", action="store_true", help="sem brain nem Piper (teste de conexao)")
    ap.add_argument("--sem-voz", action="store_true", help="so texto, sem Piper")
    args = ap.parse_args()

    if args.falso:
        cerebro, tts = CerebroFalso(), None
    else:
        cerebro = Cerebro()
        tts = None if args.sem_voz else Piper()

    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((args.host, args.porta))
    srv.listen(1)
    print("[servidor] escutando em %s:%d" % (args.host, args.porta), flush=True)
    try:
        while True:
            conn, addr = srv.accept()
            conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            print("[servidor] cliente conectado: %s:%d" % addr, flush=True)
            with conn:
                try:
                    atender(conn, cerebro, tts)
                except (OSError, ValueError) as e:
                    print("[servidor] conexao encerrada: %s" % e, flush=True)
    except KeyboardInterrupt:
        print("\n[servidor] encerrando")
    finally:
        srv.close()


if __name__ == "__main__":
    main()
