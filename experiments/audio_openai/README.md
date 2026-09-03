# experiments/audio_openai — protótipo de STT via API OpenAI

Código funcional e bem comentado de uma versão **anterior** da captura de
áudio + transcrição, feita com a **API da OpenAI** (`gpt-transcribe`).
Está aqui como referência, **fora do caminho atual**.

## Por que está parado

O pipeline atual (etapa d) usa transcrição **local/offline** com
`faster-whisper` — sem chave, sem internet, sem custo por uso. Ver
[`../../src/pc/stt.py`](../../src/pc/stt.py).

## O que tem aqui

| Arquivo | O quê |
|---|---|
| `audio.py` original | foi promovido para [`../../src/rasp/audio.py`](../../src/rasp/audio.py) (captura de mic, reusado no projeto) |
| `openai_client.py` | chamadas à API de transcrição da OpenAI, com tratamento de erro por tipo |
| `config.py` | leitura de `OPENAI_API_KEY`, nomes de modelo, timeouts |
| `testar_transcricao.py` | grava uma frase e imprime a transcrição (usa `audio.py` + `openai_client.py`) |
| `env.example.txt` | modelo de `.env` com a chave |
| `requirements.txt` | `openai`, `sounddevice`, `numpy`, `python-dotenv` |

Nota: `testar_transcricao.py` importa `audio`, que não está mais nesta
pasta. Se for reativar, aponte para `src/rasp/audio.py` ou copie de volta.

## Quando isso pode voltar

Se um dia `stt.transcrever(caminho_wav) -> str` ganhar duas implementações
intercambiáveis (local e API), o `openai_client.py` daqui é a base da
variante de API.
