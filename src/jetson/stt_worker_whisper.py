"""
stt_worker_whisper.py — processo auxiliar que transcreve fala com Whisper
(faster-whisper, offline, na CPU). Alternativa ao stt_worker.py (Vosk).

Mesmo protocolo do stt_worker.py (stdin/stdout binarios):
  - ao terminar de carregar o modelo escreve a linha "PRONTO\\n";
  - pedido: 1 byte de comando + 4 bytes big-endian com o tamanho + PCM
    S16_LE mono 16 kHz:
      S  comeca uma frase nova;
      C  mais audio da frase; responde {"parcial": ""} (o Whisper nao faz
         transcricao parcial: o texto so aparece quando a frase termina);
      E  fim da frase; responde {"texto": "...", "conf": 0.0-1.0}
         (texto vazio quando nao entendeu nada ou quando era alucinacao).

Diferente do Vosk com gramatica (que so reconhece frases do repertorio), o
Whisper entende perguntas livres. Em troca ele "alucina" em ruido/silencio
(repete letras, inventa legendas), entao o resultado passa por filtros aqui.

Configuracao por ambiente:
  LSA_WHISPER_MODEL    tiny | base | small (padrao) | medium | pasta local
  LSA_WHISPER_THREADS  threads da CPU (padrao 3)

Uso (a partir de src/, so pra teste manual):
    python -m jetson.stt_worker_whisper
"""

import math
import os
import re
import struct
import sys

import numpy as np

TAXA = 16000
MODELO_PADRAO = "small"
# vocabulario do robo: ajuda o Whisper a acertar nomes proprios e siglas
PROMPT = "Conversa com o robô C-2GELSA, do Laboratório de Sistemas Autônomos, o LSA, da PUCRS."

_PALAVRAS_PROMPT = frozenset(re.findall(r"[\wÀ-ÿ-]+", PROMPT.lower()))

# o que o Whisper escreve quando ouve silencio/ruido (legendas do treino)
_ALUCINACOES = re.compile(
    r"legendas?\b|amara\.org|inscreva|se inscreva|obrigado por assistir|"
    r"tradu[cç][aã]o|www\.|\.com\b|♪", re.IGNORECASE)
_LETRA_REPETIDA = re.compile(r"(.)\1{7,}")                 # "eeeeeeee"
_PALAVRA_REPETIDA = re.compile(r"\b(\w+)(?:[\s,.!?]+\1\b){3,}", re.IGNORECASE)  # "sim sim sim sim"


def _ler_exato(f, n):
    buf = b""
    while len(buf) < n:
        parte = f.read(n - len(buf))
        if not parte:
            return None
        buf += parte
    return buf


def _alucinacao(texto):
    t = texto.strip()
    if not t:
        return True
    if _LETRA_REPETIDA.search(t) or _PALAVRA_REPETIDA.search(t) or _ALUCINACOES.search(t):
        return True
    # devolveu o proprio prompt, inteiro ou em pedacos (acontece com audio quase
    # mudo): so tem palavras que estao nele
    palavras = re.findall(r"[\wÀ-ÿ-]+", t.lower())
    return bool(palavras) and all(p in _PALAVRAS_PROMPT for p in palavras)


def transcrever(modelo, pcm_bytes):
    """(texto, conf 0-1). Vazio/0.0 se nao entendeu ou se era alucinacao."""
    if len(pcm_bytes) < TAXA * 2 * 0.3:                       # menos de 0,3 s
        return "", 0.0
    audio = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32) / 32768.0
    segmentos, _ = modelo.transcribe(
        audio, language="pt", beam_size=1, temperature=0.0,
        condition_on_previous_text=False, initial_prompt=PROMPT,
        without_timestamps=True, vad_filter=False,
        compression_ratio_threshold=2.0, no_speech_threshold=0.6,
        log_prob_threshold=-1.0, repetition_penalty=1.1, no_repeat_ngram_size=4)
    textos, logprobs, pesos = [], [], []
    for s in segmentos:
        if s.no_speech_prob > 0.6 and s.avg_logprob < -0.8:
            continue                                          # trecho sem fala
        t = s.text.strip()
        if not t:
            continue
        textos.append(t)
        peso = max(0.2, s.end - s.start)
        logprobs.append(s.avg_logprob * peso)
        pesos.append(peso)
    texto = " ".join(textos).strip()
    if not texto or _alucinacao(texto):
        return "", 0.0
    conf = math.exp(sum(logprobs) / sum(pesos))
    return texto, round(min(1.0, conf), 3)


def main():
    from faster_whisper import WhisperModel

    nome = os.environ.get("LSA_WHISPER_MODEL") or MODELO_PADRAO
    threads = int(os.environ.get("LSA_WHISPER_THREADS") or 3)
    modelo = WhisperModel(nome, device="cpu", compute_type="int8", cpu_threads=threads)
    # aquece (1a inferencia e bem mais lenta): 1 s de silencio
    transcrever(modelo, b"\x00\x00" * TAXA)

    entrada, saida = sys.stdin.buffer, sys.stdout.buffer
    saida.write(b"PRONTO\n")
    saida.flush()

    def responder(obj):
        import json
        saida.write((json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8"))
        saida.flush()

    acumulado = []
    while True:
        cab = _ler_exato(entrada, 5)
        if cab is None:
            return
        cmd, n = cab[:1], struct.unpack(">I", cab[1:])[0]
        pcm = _ler_exato(entrada, n) if n else b""
        if pcm is None:
            return

        if cmd == b"S":
            acumulado = []
        elif cmd == b"C":
            acumulado.append(pcm)
            responder({"parcial": ""})
        elif cmd == b"E":
            try:
                texto, conf = transcrever(modelo, b"".join(acumulado))
            except Exception as e:           # nunca derruba o worker por uma frase ruim
                print("[whisper] erro: %s" % e, file=sys.stderr, flush=True)
                texto, conf = "", 0.0
            acumulado = []
            responder({"texto": texto, "conf": conf})
        else:
            responder({"erro": "comando invalido"})


if __name__ == "__main__":
    main()
