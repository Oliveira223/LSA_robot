"""
cerebro_claude.py — sessao do Claude Code local (`claude -p`) usada como cerebro de teste,
no lugar do Ollama. Um unico processo fica vivo (stream-json) e guarda a conversa: so a
primeira pergunta paga a partida (~9 s nesta Jetson), as outras respondem em ~2 s, e o
texto chega aos poucos pra o robo comecar a falar na primeira frase. Sem API key: usa o
login da maquina.

Compatibilidade: Python 3.6 da Jetson.
"""

import json
import os
import queue
import re
import subprocess
import tempfile
import threading

CONTEXTO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "contexto_claude.md")
CONTEXTO_PADRAO = ("Voce e o cerebro de um robo que conversa por voz em portugues do Brasil. "
                   "Responda em no maximo 2 frases curtas, sem markdown, sem emojis, sem listas.")
MODELO_PADRAO = "haiku"
TIMEOUT_S = 60.0
_FIM_FRASE = re.compile(r"(?<=[.!?])\s+")


class SessaoClaude:
    def __init__(self, modelo=MODELO_PADRAO):
        self.modelo = modelo
        self._lock = threading.Lock()        # uma pergunta por vez
        self._proc = None
        self._linhas = queue.Queue()

    def iniciar(self):
        """(Re)abre o processo lendo o contexto de novo, e o aquece com uma pergunta muda."""
        with self._lock:
            self._abrir()
            self._turno("Responda apenas: ok", None)

    def parar(self):
        with self._lock:
            self._fechar()

    def perguntar(self, texto, ao_frase, cancelado=lambda: False):
        """Chama ao_frase(frase) a cada frase pronta; devolve a resposta inteira."""
        with self._lock:
            if self._proc is None or self._proc.poll() is not None:
                self._abrir()
            try:
                return self._turno(texto, ao_frase, cancelado)
            except Exception:
                self._fechar()                # sessao em estado desconhecido: recomeca na proxima
                raise

    # ── interno (sempre com _lock) ─────────────────────────────────────
    def _abrir(self):
        self._fechar()
        try:
            with open(CONTEXTO, encoding="utf-8") as f:
                contexto = f.read().strip()
        except OSError:
            contexto = CONTEXTO_PADRAO
        self._linhas = queue.Queue()
        self._proc = subprocess.Popen(
            ["claude", "-p", "--input-format", "stream-json", "--output-format", "stream-json",
             "--include-partial-messages", "--verbose", "--model", self.modelo,
             "--system-prompt", contexto, "--tools", "", "--disable-slash-commands",
             "--no-session-persistence"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            universal_newlines=True, cwd=tempfile.gettempdir())     # cwd neutro: sem CLAUDE.md
        threading.Thread(target=self._ler, args=(self._proc, self._linhas), daemon=True).start()

    @staticmethod
    def _ler(proc, fila):
        for linha in proc.stdout:
            fila.put(linha)
        fila.put(None)                        # processo morreu

    def _fechar(self):
        if self._proc is not None and self._proc.poll() is None:
            self._proc.terminate()
        self._proc = None

    def _turno(self, texto, ao_frase, cancelado=lambda: False):
        self._proc.stdin.write(json.dumps({"type": "user", "message": {"role": "user",
                                                                        "content": texto}}) + "\n")
        self._proc.stdin.flush()
        pendente, resposta = "", ""

        def falar(frase):
            frase = frase.strip()
            if frase and ao_frase is not None and not cancelado():
                ao_frase(frase)

        while True:
            try:
                linha = self._linhas.get(timeout=TIMEOUT_S)
            except queue.Empty:
                raise RuntimeError("claude nao respondeu em %.0f s" % TIMEOUT_S)
            if linha is None:
                raise RuntimeError("o processo do claude encerrou")
            ev = json.loads(linha)
            if ev.get("type") == "stream_event":
                d = ev["event"].get("delta") or {}
                if d.get("type") == "text_delta":
                    pendente += d["text"]
                    resposta += d["text"]
                    *prontas, pendente = _FIM_FRASE.split(pendente)
                    for frase in prontas:
                        falar(frase)
            elif ev.get("type") == "result":
                if ev.get("is_error"):
                    raise RuntimeError("claude: %s" % str(ev.get("result"))[:200])
                falar(pendente)
                return resposta.strip()
