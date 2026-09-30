"""
transcritor.py — transcricao local (na Jetson, offline) da fala captada pelo
OuvinteVAD, pra mostrar como mensagens na tela do app da camera.

Mesma interface do jetson.chat_client.ClienteChat (mensagens() / parar()),
mas sem PC nem operador. O audio vai pro processo jetson/stt_worker.py (Vosk,
modelo pequeno em portugues) EM STREAMING, enquanto a pessoa ainda fala
(callback do OuvinteVAD): o texto aparece parcial ao vivo e, quando a frase
fecha, so falta o resto — a mensagem final ("usuario", texto) sai quase
na hora.

So transcreve fala de verdade. Sem fala ou com so ruido, nao aparece nada:
  1. o proprio VAD ja so fecha frase quando o volume passa do limiar;
  2. aqui a frase e descartada se tiver pouco trecho falado ou se nunca
     passou de um pico de volume minimo (barulho isolado, batida, ventilador);
     e o texto parcial so aparece na tela depois que o Vosk reconhece algo;
  3. o resultado do Vosk e descartado se vier vazio, com confianca baixa ou
     so com interjeicoes ("e", "eh", "hum"...), que e o que o reconhecedor
     inventa quando ouve ruido.
Tudo que for descartado vai pro log ([transcritor] descartado ...) pra
ajustar os limites olhando o uso real.

Configuracao por ambiente (opcional):
  LSA_VOSK_MODEL   pasta do modelo   (padrao ~/dev/vosk-model-small-pt-0.3)
  LSA_LIBSTDCXX    pasta com o libstdc++ novo, ver stt_worker.py
                   (padrao ~/dev/libstdcxx-new)

Compatibilidade: Python 3.6 da Jetson (sem "from __future__ import
annotations", sem "X | None").

Uso (a partir de src/):
    python3 -m jetson.transcritor     # so escuta e imprime as mensagens
"""

import audioop
import json
import os
import struct
import subprocess
import sys
import queue
import threading
import time

try:
    import numpy as np
except ImportError:  # pragma: no cover
    np = None

from jetson.mic_vad import ErroDeMic, OuvinteVAD

MAX_MENSAGENS = 8
TAXA_STT = 16000

CHUNK_S = 0.05           # tamanho dos pedacos do OuvinteVAD
DURACAO_MIN_S = 0.3      # trecho FALADO minimo (pedacos acima de PICO_MIN_RMS/2)
PICO_MIN_RMS = 800       # volume (RMS de pedacos de 50 ms) que a frase precisa atingir
CONF_MIN = 0.5           # confianca media das palavras (0-1) abaixo disso = descarta

# O que o Vosk "escuta" em ruido: interjeicoes e palavras soltas curtas.
INTERJEICOES = frozenset([
    "e", "é", "eh", "ah", "ai", "aí", "oh", "uh", "hum", "hã", "a", "o", "um", "uma",
    "eu", "de", "que", "se", "já", "ja", "né", "ne", "tá", "ta",
])

_AQUI = os.path.dirname(os.path.abspath(__file__))
_WORKER = os.path.join(_AQUI, "stt_worker.py")


class ErroDeTranscricao(Exception):
    """Falha previsivel ao montar o transcritor (modelo ou Vosk ausentes)."""


def _pasta_modelo():
    return os.environ.get("LSA_VOSK_MODEL") or os.path.expanduser("~/dev/vosk-model-small-pt-0.3")


def _env_worker():
    env = dict(os.environ)
    base = os.environ.get("LSA_LIBSTDCXX") or os.path.expanduser("~/dev/libstdcxx-new")
    dirs = [os.path.join(base, "usr/lib/aarch64-linux-gnu"),
            os.path.join(base, "lib/aarch64-linux-gnu")]
    dirs = [d for d in dirs if os.path.isdir(d)]
    if dirs:
        env["LD_LIBRARY_PATH"] = ":".join(dirs + [env.get("LD_LIBRARY_PATH", "")]).rstrip(":")
    return env


def _para_16k_mono(dados):
    """Pedaco S16_LE 48 kHz estereo -> PCM S16_LE 16 kHz mono. Faz a media de
    3 amostras (filtro passa-baixa simples) antes de decimar; o ratecv do
    audioop pega 1 a cada 3 sem filtrar e o chiado aliasado atrapalha o Vosk."""
    mono = audioop.tomono(dados, 2, 0.5, 0.5)
    if np is None:
        return audioop.ratecv(mono, 2, 1, 48000, TAXA_STT, None)[0]
    x = np.frombuffer(mono, dtype=np.int16)
    n = len(x) // 3 * 3
    return (x[:n].reshape(-1, 3).astype(np.int32).sum(axis=1) // 3).astype(np.int16).tobytes()


def _so_interjeicoes(texto):
    palavras = texto.lower().replace("-", " ").split()
    return all(p in INTERJEICOES for p in palavras)


class ClienteTranscricao:
    """
    Transcreve continuamente as frases do OuvinteVAD.

    mensagens() devolve as ultimas MAX_MENSAGENS tuplas (autor, texto) —
    sempre autor "usuario" — e, enquanto a pessoa fala, uma ultima bolha
    provisoria com o texto parcial ja reconhecido. estado() devolve
    "carregando", "pronto" ou "erro".
    """

    def __init__(self, ouvinte=None, modelo=None, sem_mic=False):
        """`sem_mic=True`: funciona so por texto (enviar_texto / comando `digitar`
        do terminal `camera`), sem ouvinte — pra quando o microfone da PrimeSense
        esta fora do ar. Um ouvinte pode entrar depois com trocar_ouvinte()."""
        self._modelo = modelo or _pasta_modelo()
        if not os.path.isdir(self._modelo):
            raise ErroDeTranscricao("modelo Vosk nao encontrado em %s" % self._modelo)

        self._ouvinte_proprio = ouvinte is None and not sem_mic
        try:
            self._ouvinte = None if sem_mic else (ouvinte or OuvinteVAD())
        except ErroDeMic as e:
            raise ErroDeTranscricao("microfone indisponivel: %s" % e)

        self._lock = threading.Lock()
        self._mensagens = []
        self._registro = []        # (seq, autor, texto) numerado, pro terminal `camera`
        self._seq = 0
        self._estado = "carregando"
        self._parcial = ""
        self._conversa = None      # ClienteConversa (Jetson 1), opcional
        self._mudo_extra = None
        self._estava_mudo = False
        self._rodando = True
        self._proc = None
        self._eventos = queue.Queue()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        # o OuvinteVAD continua enfileirando cada frase inteira em proxima_fala();
        # ninguem mais consome isso aqui, entao esvazia pra nao acumular memoria.
        self._drenagem = threading.Thread(target=self._drenar, daemon=True)
        self._drenagem.start()
        if self._ouvinte is not None:
            self._ouvinte.definir_ao_audio(self._ao_audio)

    # ── consulta (thread do video) ─────────────────────────────────────
    def mensagens(self):
        with self._lock:
            msgs = list(self._mensagens)
            if self._parcial:
                msgs.append(("usuario", self._parcial))
            return msgs

    def estado(self):
        with self._lock:
            return self._estado

    def definir_conversa(self, conversa):
        """Liga ao cerebro da Jetson 1 (jetson/conversa_client.py): cada frase
        transcrita e enviada a ele e o microfone fica mudo enquanto o robo fala."""
        self._conversa = conversa

    def trocar_ouvinte(self, novo):
        """Passa a escutar `novo` (um OuvinteVAD) no lugar do atual, ou nenhum (None).
        Quem chama e dono dos ouvintes: para o antigo e cria o novo."""
        antigo = self._ouvinte
        if antigo is not None:
            antigo.definir_ao_audio(None)
        self._ouvinte = novo
        self._ouvinte_proprio = False
        self._estava_mudo = False
        if novo is not None:
            novo.definir_ao_audio(self._ao_audio)

    def registro_desde(self, seq):
        """(ultimo_seq, [(autor, texto), ...], parcial): as mensagens surgidas depois
        de `seq` (0 = todas as guardadas) e o texto parcial de uma fala em curso.
        Usado por `transcrever`/`conversar` do terminal `camera`."""
        with self._lock:
            itens = [(a, t) for (n, a, t) in self._registro if n > seq]
            return self._seq, itens, self._parcial

    def definir_mudo_extra(self, funcao):
        """funcao() -> True enquanto o robo fala por outro caminho (ex.: a
        saudacao por proximidade), pra o mic nao transcrever a propria voz."""
        self._mudo_extra = funcao

    def adicionar(self, autor, texto):
        """Acrescenta uma bolha ("usuario" ou "robo") a conversa mostrada na tela."""
        with self._lock:
            self._mensagens.append((autor, texto))
            if len(self._mensagens) > MAX_MENSAGENS:
                self._mensagens.pop(0)
            self._seq += 1
            self._registro.append((self._seq, autor, texto))
            if len(self._registro) > 200:
                self._registro.pop(0)

    def enviar_texto(self, texto):
        """Injeta `texto` como se tivesse sido falado (bolha do usuario + envio
        ao cerebro). Usado pelo comando `digitar` do terminal `camera`."""
        self._adicionar(texto)

    def _adicionar(self, texto):
        self.adicionar("usuario", texto)
        if self._conversa is not None:
            self._conversa.enviar(texto)

    def _set(self, estado=None, parcial=None):
        with self._lock:
            if estado is not None:
                self._estado = estado
            if parcial is not None:
                self._parcial = parcial

    def _ao_audio(self, evento, dados):
        # roda na thread do mic: so enfileira
        if self._estado != "pronto":
            return
        if ((self._conversa is not None and self._conversa.falando())
                or (self._mudo_extra is not None and self._mudo_extra())):
            # o robo esta falando: o mic ouviria a propria voz dele
            if not self._estava_mudo:
                self._estava_mudo = True
                self._eventos.put(("mudo", None))
            return
        self._estava_mudo = False
        self._eventos.put((evento, dados))

    def _drenar(self):
        while self._rodando:
            ouvinte = self._ouvinte          # pode ser trocado/None a qualquer momento
            if ouvinte is None:
                time.sleep(0.5)
                continue
            ouvinte.proxima_fala(timeout=1.0)

    # ── worker ─────────────────────────────────────────────────────────
    def _iniciar_worker(self):
        self._proc = subprocess.Popen(
            [sys.executable, _WORKER, self._modelo],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            env=_env_worker())
        linha = self._proc.stdout.readline()
        if linha.strip() != b"PRONTO":
            raise ErroDeTranscricao("Vosk nao iniciou (worker saiu sem dizer PRONTO)")

    def _pedir(self, cmd, pcm=b""):
        """Manda um comando ao worker e devolve a resposta (dict). Todo C e E
        tem exatamente uma resposta; o S nao tem."""
        self._proc.stdin.write(cmd + struct.pack(">I", len(pcm)) + pcm)
        self._proc.stdin.flush()
        if cmd == b"S":
            return None
        linha = self._proc.stdout.readline()
        if not linha:
            raise ErroDeTranscricao("worker do Vosk encerrou")
        return json.loads(linha.decode("utf-8"))

    def _loop(self):
        try:
            self._iniciar_worker()
        except (ErroDeTranscricao, OSError) as e:
            print("[transcritor] %s" % e, flush=True)
            self._set(estado="erro")
            return
        self._set(estado="pronto")
        print("[transcritor] pronto (modelo %s)" % os.path.basename(self._modelo), flush=True)

        ativa = False
        pico = falados = 0
        try:
            while self._rodando:
                try:
                    evento, dados = self._eventos.get(timeout=1.0)
                except queue.Empty:
                    continue

                if evento == "mudo":
                    ativa = False
                    self._set(parcial="")
                    continue
                if evento == "inicio":
                    self._pedir(b"S")
                    ativa, pico, falados = True, 0, 0
                    evento = "chunk"
                if evento == "chunk" and ativa:
                    # junta o que ja estiver na fila num envio so (se o Vosk
                    # atrasar, recupera em vez de acumular ida-e-volta)
                    pedacos = [dados]
                    while True:
                        try:
                            ev2, d2 = self._eventos.get_nowait()
                        except queue.Empty:
                            break
                        if ev2 == "chunk":
                            pedacos.append(d2)
                        else:               # inicio/fim: volta pra fila em ordem
                            self._eventos.queue.appendleft((ev2, d2))
                            break
                    for p in pedacos:
                        for i in range(0, len(p), 9600):
                            r = audioop.rms(p[i:i + 9600], 2)
                            pico = max(pico, r)
                            falados += r >= PICO_MIN_RMS / 2
                    resp = self._pedir(b"C", _para_16k_mono(b"".join(pedacos)))
                    parcial = resp.get("parcial", "")
                    self._set(parcial=parcial if parcial and not _so_interjeicoes(parcial) else "")
                elif evento == "fim" and ativa:
                    ativa = False
                    t0 = time.time()
                    resp = self._pedir(b"E")
                    self._set(parcial="")
                    texto, conf = resp.get("texto", ""), float(resp.get("conf", 0.0))
                    dur = falados * CHUNK_S
                    if dur < DURACAO_MIN_S or pico < PICO_MIN_RMS:
                        print("[transcritor] descartado (ruido) falado=%.2fs pico=%d texto=%r" % (
                            dur, pico, texto), flush=True)
                    elif not texto or conf < CONF_MIN or _so_interjeicoes(texto):
                        print("[transcritor] descartado %r conf=%.2f falado=%.2fs pico=%d" % (
                            texto, conf, dur, pico), flush=True)
                    else:
                        print("[transcritor] %r conf=%.2f (final %.2fs apos o fim da fala)" % (
                            texto, conf, time.time() - t0), flush=True)
                        self._adicionar(texto[0].upper() + texto[1:])
        except (ErroDeTranscricao, OSError, ValueError) as e:
            print("[transcritor] erro: %s" % e, flush=True)
            self._set(estado="erro", parcial="")

    def parar(self):
        self._rodando = False
        if self._ouvinte is not None:
            self._ouvinte.definir_ao_audio(None)
            if self._ouvinte_proprio:
                self._ouvinte.parar()
        if self._proc is not None:
            try:
                self._proc.stdin.close()
                self._proc.terminate()
            except Exception:
                pass
        self._thread.join(timeout=2)


if __name__ == "__main__":
    print("[transcritor] iniciando — fale perto do mic (Ctrl-C sai)", flush=True)
    try:
        cliente = ClienteTranscricao()
    except ErroDeTranscricao as e:
        print("[transcritor] %s" % e, flush=True)
        raise SystemExit(1)
    vistas = 0
    try:
        while True:
            msgs = cliente.mensagens()[:-1] if cliente._parcial else cliente.mensagens()
            for _, texto in msgs[vistas:]:
                print("[voce] %s" % texto, flush=True)
            vistas = len(msgs)
            time.sleep(0.2)
    except KeyboardInterrupt:
        pass
    finally:
        cliente.parar()
