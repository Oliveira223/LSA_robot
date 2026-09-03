"""
stt.py — transcrição de fala (Speech-To-Text) no PC, com faster-whisper.

Roda 100% local: o modelo é baixado uma vez para ~/.cache e depois funciona
offline, sem chave de API e sem custo por uso. Isola o resto do código de
qualquer detalhe do faster-whisper — quem chama usa só transcrever(caminho).

Uma implementação alternativa via API OpenAI existe, parada, em
experiments/audio_openai/openai_client.py; se um dia as duas conviverem,
elas devem expor a mesma função transcrever(caminho_wav) -> str.
"""

from __future__ import annotations

import os

# Tamanho do modelo: "tiny" | "base" | "small" | "medium" | "large-v3".
# "small" dá boa precisão em português num notebook sem GPU; "base" é o
# fallback mais leve. Trocar sem mexer no código:  export LSA_WHISPER_MODEL=base
MODELO = os.environ.get("LSA_WHISPER_MODEL", "small")

# int8 em CPU: rápido o suficiente e sem depender de GPU/CUDA.
_DEVICE = os.environ.get("LSA_WHISPER_DEVICE", "cpu")
_COMPUTE = os.environ.get("LSA_WHISPER_COMPUTE", "int8")

_IDIOMA = "pt"

# O modelo é pesado para carregar; guardamos numa variável de módulo e
# reaproveitamos entre transcrições (mesmo padrão do cliente da OpenAI).
_modelo = None


class ErroDeSTT(Exception):
    """Falha previsível ao transcrever (arquivo ausente/vazio, modelo indisponível)."""
    pass


def _obter_modelo():
    global _modelo
    if _modelo is None:
        try:
            from faster_whisper import WhisperModel
        except ImportError as erro:
            raise ErroDeSTT(
                "faster-whisper não instalado. Rode:\n"
                "  pip install -r src/pc/requirements.txt"
            ) from erro

        print(f"[stt] carregando modelo '{MODELO}' ({_DEVICE}/{_COMPUTE})... "
              "a primeira vez baixa o modelo.", flush=True)
        try:
            _modelo = WhisperModel(MODELO, device=_DEVICE, compute_type=_COMPUTE)
        except Exception as erro:
            raise ErroDeSTT(f"não foi possível carregar o modelo '{MODELO}': {erro}") from erro
        print("[stt] modelo pronto.", flush=True)

    return _modelo


def transcrever(caminho_wav: str) -> str:
    """
    Transcreve um arquivo WAV e devolve o texto (string vazia se não houver fala).

    Levanta ErroDeSTT para falhas previsíveis.
    """
    if not caminho_wav or not os.path.exists(caminho_wav):
        raise ErroDeSTT(f"arquivo de áudio não encontrado: {caminho_wav}")
    if os.path.getsize(caminho_wav) < 2000:
        raise ErroDeSTT("áudio vazio ou corrompido (arquivo só com cabeçalho).")

    modelo = _obter_modelo()

    try:
        segmentos, _info = modelo.transcribe(caminho_wav, language=_IDIOMA)
        texto = " ".join(s.text.strip() for s in segmentos).strip()
    except Exception as erro:
        raise ErroDeSTT(f"falha na transcrição: {erro}") from erro

    return texto


if __name__ == "__main__":
    # Teste rápido:  python -m pc.stt caminho/do/audio.wav
    import sys

    if len(sys.argv) < 2:
        print("uso: python -m pc.stt <arquivo.wav>")
        raise SystemExit(2)
    try:
        print(repr(transcrever(sys.argv[1])))
    except ErroDeSTT as e:
        print(f"[ERRO] {e}")
        raise SystemExit(1)
