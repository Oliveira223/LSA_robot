"""
openai_client.py — Toda a comunicação com a API da OpenAI.

Isolar isto num módulo próprio significa que o main.py nunca precisa saber
nomes de endpoint, nomes de exceção do SDK ou detalhes de retry. Ele só
chama transcrever() e recebe texto ou um ErroDaAPI com mensagem legível.
"""

import os

import openai
from openai import OpenAI

import config


class ErroDaAPI(Exception):
    """Falha ao falar com a OpenAI, já traduzida para uma mensagem clara."""
    pass


# Guardamos o cliente numa variável de módulo para reaproveitar a conexão
# HTTP entre chamadas. Criar um cliente novo a cada pergunta desperdiçaria
# tempo refazendo o handshake TLS — algo que pesa na CPU da Pi 3.
_cliente = None


def obter_cliente():
    """Cria o cliente na primeira chamada e reaproveita nas seguintes."""
    global _cliente

    if _cliente is None:
        chave = config.obter_api_key()   # levanta ErroDeConfiguracao se faltar
        _cliente = OpenAI(
            api_key=chave,
            timeout=config.TIMEOUT_SEGUNDOS,
            max_retries=config.MAX_TENTATIVAS,
        )

    return _cliente


def _validar_arquivo(caminho_wav):
    """
    Checagens locais antes de gastar upload e crédito.

    Cada uma destas falhas aconteceria de qualquer jeito na API, mas custaria
    uma requisição, alguns segundos e uma mensagem de erro muito pior.
    """
    if not caminho_wav or not os.path.exists(caminho_wav):
        raise ErroDaAPI(f"Arquivo de áudio não encontrado: {caminho_wav}")

    tamanho = os.path.getsize(caminho_wav)

    if tamanho < config.TAMANHO_MINIMO_BYTES:
        raise ErroDaAPI(
            f"Áudio vazio ou corrompido ({tamanho} bytes). "
            "A gravação provavelmente falhou."
        )

    limite = config.LIMITE_TAMANHO_MB * 1024 * 1024
    if tamanho > limite:
        raise ErroDaAPI(
            f"Áudio grande demais ({tamanho / 1024 / 1024:.1f} MB). "
            f"O limite da API é {config.LIMITE_TAMANHO_MB} MB."
        )


def transcrever(caminho_wav):
    """
    Envia o WAV para /v1/audio/transcriptions e devolve o texto.

    Retorna string vazia se o modelo não identificou fala nenhuma —
    quem chama decide o que fazer nesse caso.
    """
    _validar_arquivo(caminho_wav)
    cliente = obter_cliente()

    print("[INFO] Enviando áudio para transcrição.", flush=True)

    try:
        # 'rb' = read binary. Áudio não é texto; abrir em modo texto
        # corromperia os bytes. O 'with' garante que o arquivo feche
        # mesmo se a requisição falhar no meio.
        with open(caminho_wav, "rb") as arquivo:
            resultado = cliente.audio.transcriptions.create(
                model=config.MODELO_TRANSCRICAO,
                file=arquivo,
                # 'languages' é específico do gpt-transcribe e vai por
                # extra_body. Dizer que esperamos português evita que
                # frases curtas sejam confundidas com espanhol.
                extra_body={"languages": config.IDIOMAS_ESPERADOS},
            )

    # --- Ordem importa: do erro mais específico para o mais genérico ------
    # APITimeoutError herda de APIConnectionError; AuthenticationError,
    # RateLimitError e BadRequestError herdam de APIStatusError. Se você
    # inverter a ordem, o except genérico captura tudo antes.

    except openai.AuthenticationError as erro:
        raise ErroDaAPI(
            "Chave de API rejeitada pela OpenAI (401).\n"
            "Verifique se a chave está correta e ainda ativa em\n"
            "https://platform.openai.com/api-keys"
        ) from erro

    except openai.PermissionDeniedError as erro:
        raise ErroDaAPI(
            f"Sem permissão para usar o modelo '{config.MODELO_TRANSCRICAO}' (403).\n"
            "Confirme se o projeto tem esse modelo habilitado."
        ) from erro

    except openai.RateLimitError as erro:
        raise ErroDaAPI(
            "Limite de uso atingido (429).\n"
            "Aguarde alguns segundos ou verifique o saldo em\n"
            "https://platform.openai.com/settings/organization/billing"
        ) from erro

    except openai.BadRequestError as erro:
        raise ErroDaAPI(
            f"A API rejeitou a requisição (400): {erro}\n"
            "Normalmente indica formato de áudio inválido ou parâmetro errado."
        ) from erro

    except openai.APITimeoutError as erro:
        raise ErroDaAPI(
            f"A API demorou mais de {config.TIMEOUT_SEGUNDOS:.0f}s para responder.\n"
            "Conexão lenta? Tente aumentar TIMEOUT_SEGUNDOS em config.py."
        ) from erro

    except openai.APIConnectionError as erro:
        raise ErroDaAPI(
            "Não foi possível conectar à API da OpenAI.\n"
            "Verifique a internet da Jetson com:  ping -c 3 api.openai.com"
        ) from erro

    except openai.APIStatusError as erro:
        raise ErroDaAPI(
            f"A API retornou erro {erro.status_code}: {erro}"
        ) from erro

    except openai.APIError as erro:
        raise ErroDaAPI(f"Erro inesperado da API: {erro}") from erro

    print("[INFO] Transcrição recebida.", flush=True)

    # resultado.text pode vir None se o modelo não achou fala.
    return (resultado.text or "").strip()
