"""
stt_worker.py — processo auxiliar que transcreve fala com Vosk (offline).

Roda separado do app da camera porque o libvosk.so (wheel do PyPI) foi
compilado com GCC 9+ e precisa de um libstdc++ mais novo que o do Ubuntu
18.04 do JetPack; o jetson/transcritor.py lanca este processo com
LD_LIBRARY_PATH apontando pro libstdc++ novo, sem mexer no resto do sistema
(nem no cv2/OpenNI2 do app). Tambem isola qualquer crash do reconhecedor.

Protocolo (stdin/stdout binarios). O audio chega EM PEDACOS enquanto a
pessoa ainda fala, pra a transcricao ficar pronta logo que ela para:
  - ao terminar de carregar o modelo escreve a linha "PRONTO\\n";
  - pedido: 1 byte de comando + 4 bytes big-endian com o tamanho + PCM
    S16_LE mono 16 kHz (vazio no comando S):
      S  comeca uma frase nova (descarta qualquer frase em andamento);
      C  mais audio da frase; responde {"parcial": "texto ate agora"};
      E  fim da frase; responde {"texto": "...", "conf": 0.0-1.0}
         (texto vazio quando nao entendeu nada).
  - cada resposta e uma linha JSON.
O Vosk fecha "segmentos" sozinho em pausas curtas dentro da mesma frase;
todos sao acumulados (so pegar o ultimo perdia o comeco da frase).

Compatibilidade: Python 3.6 da Jetson.

Frases da visita: se existir jetson/gramatica.json (gerado por
gerar_gramatica.py a partir dos gatilhos do repertorio da Jetson 1), o Vosk
so reconhece aquelas frases e o resto vira [unk] (descartado). Com gente
conversando em volta isso troca "frase inventada" por "nao ouvi nada" e
acerta melhor as perguntas conhecidas; perguntas livres (Ollama) por voz
deixam de funcionar. LSA_VOSK_GRAMATICA=off desliga; =<arquivo> usa outro.

Uso (a partir de src/, so pra teste manual):
    LD_LIBRARY_PATH=... python -m jetson.stt_worker <pasta-do-modelo>
"""

import json
import os
import re
import struct
import sys

TAXA = 16000
GRAMATICA_PADRAO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "gramatica.json")
# a sigla falada sai soletrada; o repertorio da Jetson 1 espera "lsa"
_SIGLAS = [(re.compile(r"\b[eé]le [eé]sse [aá]\b"), "lsa")]


class _Gramatica:
    """Rele o arquivo quando ele muda (gerar_gramatica.py de novo vale na proxima
    frase, sem reiniciar o app — reiniciar costuma travar o mic da PrimeSense)."""

    def __init__(self):
        self._caminho = os.environ.get("LSA_VOSK_GRAMATICA") or GRAMATICA_PADRAO
        self._mtime, self._texto = None, None

    def atual(self):
        if self._caminho == "off" or not os.path.isfile(self._caminho):
            return None
        mtime = os.path.getmtime(self._caminho)
        if mtime != self._mtime:
            self._mtime = mtime
            try:
                with open(self._caminho, encoding="utf-8") as f:
                    texto = f.read()
                json.loads(texto)          # arquivo pela metade/invalido: fica com a anterior
                self._texto = texto
            except (OSError, ValueError):
                pass
        return self._texto


def _limpar(texto):
    texto = " ".join(p for p in texto.split() if p != "[unk]")
    for padrao, troca in _SIGLAS:
        texto = padrao.sub(troca, texto)
    return texto


def _ler_exato(f, n):
    buf = b""
    while len(buf) < n:
        parte = f.read(n - len(buf))
        if not parte:
            return None
        buf += parte
    return buf


def main():
    from vosk import KaldiRecognizer, Model, SetLogLevel

    SetLogLevel(-1)
    modelo = Model(sys.argv[1])
    gramatica = _Gramatica()
    entrada = sys.stdin.buffer
    saida = sys.stdout.buffer
    saida.write(b"PRONTO\n")
    saida.flush()

    rec = None
    textos = []      # segmentos ja fechados pelo Vosk nesta frase
    confs = []       # confianca de cada palavra desses segmentos

    def fechar_segmento(r):
        t = _limpar((r.get("text") or "").strip())
        if t:
            textos.append(t)
        for p in r.get("result") or []:
            if p.get("word") != "[unk]":
                confs.append(p.get("conf", 0.0))

    def responder(obj):
        saida.write((json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8"))
        saida.flush()

    while True:
        cab = _ler_exato(entrada, 5)
        if cab is None:
            return
        cmd, n = cab[:1], struct.unpack(">I", cab[1:])[0]
        pcm = _ler_exato(entrada, n) if n else b""
        if pcm is None:
            return

        if cmd == b"S":
            g = gramatica.atual()
            try:
                rec = KaldiRecognizer(modelo, TAXA, g) if g else KaldiRecognizer(modelo, TAXA)
            except Exception:
                rec = KaldiRecognizer(modelo, TAXA)
            rec.SetWords(True)
            del textos[:], confs[:]
        elif cmd == b"C" and rec is not None:
            if rec.AcceptWaveform(pcm):
                fechar_segmento(json.loads(rec.Result()))
                parcial = ""
            else:
                parcial = _limpar(json.loads(rec.PartialResult()).get("partial", ""))
            responder({"parcial": " ".join(textos + ([parcial] if parcial else []))})
        elif cmd == b"E" and rec is not None:
            fechar_segmento(json.loads(rec.FinalResult()))
            conf = sum(confs) / len(confs) if confs else 0.0
            responder({"texto": " ".join(textos), "conf": round(conf, 3)})
            rec = None
        else:
            responder({"erro": "comando invalido"})


if __name__ == "__main__":
    main()
