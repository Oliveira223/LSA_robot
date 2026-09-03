"""
stt.py — transcrição de fala (Speech-To-Text) no PC, com faster-whisper.

Roda 100% local: o modelo é baixado uma vez para ~/.cache e depois funciona
offline, sem chave de API e sem custo por uso. Isola o resto do código de
qualquer detalhe do faster-whisper — quem chama usa transcrever(caminho) ou
transcrever_array(amostras).

Uma implementação alternativa via API OpenAI existe, parada, em
experiments/audio_openai/openai_client.py; se um dia as duas conviverem,
elas devem expor a mesma função transcrever(...) -> str.
"""

from __future__ import annotations

import os

# O backend "xet" de download da HuggingFace trava em redes que cortam
# conexão longa (visto na rede da PUC: pega alguns MB e morre). O download
# HTTPS clássico é mais lento mas retoma de onde parou de forma confiável.
# setdefault: dá pra reativar com  export HF_HUB_DISABLE_XET=0
os.environ.setdefault("HF_HUB_DISABLE_XET", "1")

# Tamanho do modelo: "tiny" | "base" | "small" | "medium" | "large-v3".
# "small" dá boa precisão em português num notebook sem GPU; "base"/"tiny"
# baixam mais rápido. Trocar sem mexer no código:  export LSA_WHISPER_MODEL=base
MODELO = os.environ.get("LSA_WHISPER_MODEL", "small")

# int8 em CPU: rápido o suficiente e sem depender de GPU/CUDA.
_DEVICE = os.environ.get("LSA_WHISPER_DEVICE", "cpu")
_COMPUTE = os.environ.get("LSA_WHISPER_COMPUTE", "int8")

_IDIOMA = "pt"
TAXA_ALVO = 16000   # faster-whisper espera áudio a 16 kHz

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


def _consumir(segmentos, info=None, progresso=None) -> str:
    """
    faster-whisper devolve um gerador preguiçoso de segmentos — a transcrição
    só acontece de fato ao percorrê-lo. Junta o texto de todos e, se `progresso`
    for dado, chama-o com uma fração 0..1 (fim do segmento / duração do áudio)
    à medida que avança.
    """
    duracao = getattr(info, "duration", 0.0) or 0.0
    partes = []
    for s in segmentos:
        partes.append(s.text.strip())
        if progresso and duracao:
            try:
                progresso(min(max(s.end / duracao, 0.0), 1.0))
            except Exception:
                pass
    if progresso:
        try:
            progresso(1.0)
        except Exception:
            pass
    return " ".join(p for p in partes if p).strip()


def transcrever(caminho_wav: str, progresso=None) -> str:
    """
    Transcreve um arquivo WAV e devolve o texto (string vazia se não houver fala).

    `progresso`: callback opcional que recebe uma fração 0..1 durante a transcrição.
    Levanta ErroDeSTT para falhas previsíveis.
    """
    if not caminho_wav or not os.path.exists(caminho_wav):
        raise ErroDeSTT(f"arquivo de áudio não encontrado: {caminho_wav}")
    if os.path.getsize(caminho_wav) < 2000:
        raise ErroDeSTT("áudio vazio ou corrompido (arquivo só com cabeçalho).")

    modelo = _obter_modelo()
    try:
        segmentos, info = modelo.transcribe(caminho_wav, language=_IDIOMA,
                                            temperature=0)
        return _consumir(segmentos, info, progresso)
    except Exception as erro:
        raise ErroDeSTT(f"falha na transcrição: {erro}") from erro


def transcrever_array(amostras, taxa: int = TAXA_ALVO, progresso=None) -> str:
    """
    Transcreve um sinal já em memória: numpy float32 mono, amplitude em [-1, 1].

    Se `taxa` != 16 kHz, faz uma reamostragem linear simples (suficiente para
    fala destinada a STT). Evita gravar um WAV temporário só para reabrir.
    `progresso`: callback opcional que recebe uma fração 0..1 durante a transcrição.
    """
    import numpy as np

    amostras = np.asarray(amostras, dtype=np.float32).reshape(-1)
    if amostras.size == 0:
        return ""

    if taxa != TAXA_ALVO:
        n_alvo = int(round(amostras.size * TAXA_ALVO / taxa))
        if n_alvo <= 0:
            return ""
        x_velho = np.linspace(0.0, 1.0, amostras.size, endpoint=False)
        x_novo = np.linspace(0.0, 1.0, n_alvo, endpoint=False)
        amostras = np.interp(x_novo, x_velho, amostras).astype(np.float32)

    modelo = _obter_modelo()
    try:
        # temperature=0: sem a escada de "tenta de novo com temperatura maior"
        # quando o modelo fica inseguro — mais rápido e com latência previsível.
        segmentos, info = modelo.transcribe(amostras, language=_IDIOMA,
                                            temperature=0)
        return _consumir(segmentos, info, progresso)
    except Exception as erro:
        raise ErroDeSTT(f"falha na transcrição: {erro}") from erro


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
