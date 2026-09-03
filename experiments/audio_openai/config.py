"""
config.py — Configuração central do projeto.

Concentra num só lugar tudo que é "ajustável": nomes de modelo, timeouts,
duração de gravação e a leitura da API Key. Assim, mudar de modelo ou
aumentar um timeout é editar uma linha, não caçar valores espalhados.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

# Path(__file__) é o caminho deste arquivo. .resolve() transforma em caminho
# absoluto e .parent pega a pasta. Isso faz o .env ser encontrado mesmo que
# você rode o programa de outro diretório.
PASTA_DO_PROJETO = Path(__file__).resolve().parent

# Carrega o .env, se existir. Por padrão o load_dotenv NÃO sobrescreve
# variáveis já definidas no ambiente — então o export do ~/.bashrc tem
# prioridade sobre o arquivo. É o comportamento que queremos.
load_dotenv(PASTA_DO_PROJETO / ".env")

# --- Modelos da OpenAI ----------------------------------------------------
# Modelo recomendado para transcrever fala gravada no idioma original.
MODELO_TRANSCRICAO = "gpt-transcribe"

# Família GPT-5.6: 'sol' (capacidade máxima), 'terra' (equilíbrio),
# 'luna' (eficiente, alto volume). Para um assistente de voz que responde
# perguntas curtas, 'luna' dá a menor latência e o menor custo.
# Troque por "gpt-5.6-terra" se quiser respostas mais elaboradas.
MODELO_LINGUAGEM = "gpt-5.6-luna"

# Idiomas esperados na fala. Com gpt-transcribe usa-se 'languages' (lista),
# que substituiu o antigo 'language' (string). Não envie os dois.
IDIOMAS_ESPERADOS = ["pt"]

# --- Rede -----------------------------------------------------------------
# 60s é generoso: a Pi 3 no Wi-Fi pode demorar para subir o arquivo.
TIMEOUT_SEGUNDOS = 60.0

# O SDK reenvia automaticamente em falhas transitórias (erros 5xx, conexão
# caída). 2 tentativas extras é o padrão e é suficiente.
MAX_TENTATIVAS = 2

# --- Áudio ----------------------------------------------------------------
DURACAO_GRAVACAO = 5          # segundos (vira detecção de silêncio na Etapa 6)
LIMITE_TAMANHO_MB = 25        # limite da API
TAMANHO_MINIMO_BYTES = 2000   # abaixo disso o WAV só tem cabeçalho


class ErroDeConfiguracao(Exception):
    """Problema de configuração — chave ausente, malformada, etc."""
    pass


def obter_api_key():
    """
    Lê e valida a OPENAI_API_KEY.

    Falhar aqui, cedo e com mensagem clara, é muito melhor do que descobrir
    o problema só depois de gravar o áudio e receber um 401 da API.
    """
    chave = os.environ.get("OPENAI_API_KEY", "").strip()

    if not chave:
        raise ErroDeConfiguracao(
            "OPENAI_API_KEY não configurada.\n"
            "  Opção 1: adicione ao ~/.bashrc a linha\n"
            '            export OPENAI_API_KEY="sua_chave"\n'
            "           e rode: source ~/.bashrc\n"
            f"  Opção 2: crie o arquivo {PASTA_DO_PROJETO / '.env'} com\n"
            "            OPENAI_API_KEY=sua_chave"
        )

    if not chave.startswith("sk-"):
        raise ErroDeConfiguracao(
            "OPENAI_API_KEY parece inválida (deveria começar com 'sk-').\n"
            "Verifique se você não copiou aspas ou espaços junto com a chave."
        )

    return chave
