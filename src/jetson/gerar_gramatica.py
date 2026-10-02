"""
gerar_gramatica.py — gera jetson/gramatica.json (as frases que o Vosk aceita no
modo "frases da visita", ver stt_worker.py) a partir dos gatilhos do
repertorio do cerebro da Jetson 1.

Os gatilhos estao sem acento e o Vosk so aceita palavras do vocabulario dele
(com acento), entao cada palavra e trocada pelas formas acentuadas que existem
no modelo (ex.: "e" -> "e", "é"). Gatilho com palavra fora do vocabulario e
pulado; "lsa" vira "éle ésse á" (o stt_worker desfaz isso).

Rodar de novo depois de mudar o repertorio da Jetson 1:
    ssh lsa-robot1@192.168.0.103 'cd ~/Bitnet_TTS && python3 -' > /tmp/gatilhos.json <<'PY'
import repertorio, brain, json
g = [t for gs, _ in repertorio.FRASES for t in gs]
# perguntas que o brain.py responde por regra (capitais, contas), fora do repertorio
g += ["qual a capital %s %s" % (p, k) for k in brain.CAPITAIS for p in ("de", "do", "da", "dos", "das")]
g += ["qual a capital " + c for cs, _, _ in brain.CAPITAIS_ESTADOS for c in cs]
n = list(brain._NUMEROS_POR_EXTENSO)
g += ["quanto e %s %s %s" % (a, op, b) for a in n for b in n for op in ("mais", "menos", "vezes", "dividido por")]
print(json.dumps(g))
PY
    python3 jetson/gerar_gramatica.py /tmp/gatilhos.json     # a partir de src/
"""

import collections
import itertools
import json
import os
import struct
import sys
import unicodedata

MODELO = os.path.expanduser("~/dev/vosk-model-small-pt-0.3")
SAIDA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "gramatica.json")
TROCAS = {"lsa": "éle ésse á"}
# palavras curtas/comuns com forma certa conhecida (sem isso as combinacoes de
# acento explodem e frases como "qual é o seu nome" ficavam de fora)
FORMAS = {"e": ["é", "e"], "a": ["a"], "o": ["o"], "os": ["os"], "as": ["as"], "voce": ["você"],
          "esta": ["está"], "ta": ["tá"], "robo": ["robô"], "nao": ["não"], "sao": ["são"],
          "ola": ["olá"], "tambem": ["também"], "ja": ["já"], "so": ["só"], "ai": ["aí"]}
MAX_VARIANTES = 24    # combinacoes de acentos por gatilho


def _sem_acento(s):
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")


def vocabulario(modelo=MODELO):
    """Le a tabela de simbolos (OpenFst) embutida no Gr.fst do modelo pequeno."""
    with open(os.path.join(modelo, "Gr.fst"), "rb") as f:
        d = f.read()
    p = d.find(struct.pack("<i", 5) + b"<eps>")
    _disp, n = struct.unpack("<qq", d[p - 16:p])
    palavras = []
    for _ in range(n):
        tam, = struct.unpack("<i", d[p:p + 4])
        palavras.append(d[p + 4:p + 4 + tam].decode("utf-8"))
        p += 4 + tam + 8
    return palavras


def main():
    gatilhos = json.load(open(sys.argv[1], encoding="utf-8"))
    formas = collections.defaultdict(set)
    for w in vocabulario():
        formas[_sem_acento(w)].add(w)
    frases, pulados = set(), []
    for g in gatilhos:
        palavras = " ".join(TROCAS.get(w, w) for w in g.split()).split()
        opcoes = [FORMAS.get(w) or sorted(formas.get(_sem_acento(w), ()))[:3] for w in palavras]
        if not all(opcoes):
            pulados.append(g)
            continue
        frases.update(" ".join(c) for c in itertools.islice(itertools.product(*opcoes), MAX_VARIANTES))
    # escreve inteiro e troca de uma vez: o stt_worker rele o arquivo quando ele
    # muda, e ler pela metade derrubava o reconhecimento (visto 2026-10-01)
    with open(SAIDA + ".tmp", "w", encoding="utf-8") as f:
        json.dump(sorted(frases) + ["[unk]"], f, ensure_ascii=False)
    os.replace(SAIDA + ".tmp", SAIDA)
    print("%d gatilhos -> %d frases em %s; %d pulados (palavra fora do vocabulario): %s"
          % (len(gatilhos), len(frases), SAIDA, len(pulados), ", ".join(pulados)))


if __name__ == "__main__":
    main()
