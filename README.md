# LSA_robot — pipeline de comunicação Rasp ↔ PC

Robô que ouve áudio, manda para o PC, o PC processa (transcrição + resposta)
e devolve texto; o Raspberry Pi converte esse texto em voz e toca num speaker.

O Raspberry Pi é só **"ouvido e boca"** (captura de microfone + reprodução no
speaker). Transcrição, "cérebro" e qualquer processamento pesado rodam **no PC**.

```
[Mic no Rasp] → rede → [PC: STT + resposta] → texto → rede → [Rasp: TTS] → [Speaker]
```

## Progressão (uma etapa por vez, cada uma testável)

| Etapa | O que valida | Status |
|---|---|---|
| **(a)** | dois terminais no mesmo PC (localhost), só texto | ✅ feito |
| **(b)** | mesmo código, via túnel SSH entre PC e Raspberry Pi | ✅ feito |
| **(c)** | mesmo código, via WiFi direto (IP real na LAN) | ⏸️ adiada (rede da PUC — ver [docs/transporte.md](docs/transporte.md)) |
| **(d)** | integração de áudio (captura → transcrição → resposta → TTS) | 🚧 em desenvolvimento |

Na etapa (d), enquanto não há IA, um **operador humano** lê a transcrição no
terminal do PC e digita a resposta. Depois o operador é trocado pela IA.
Histórico e decisões em [PROGRESSO.md](PROGRESSO.md).

## Estrutura

```
docs/                roadmaps, to-do e a referência do transporte
  transporte.md        formato de fio + como rodar as etapas a/b (o que já funciona)
hardware/stl/         peças 3D do robô (InMoov, CC BY-NC) — sem código
src/                  todo o código executável; rode a partir daqui, via python3 -m
  common/protocol.py    enquadramento de mensagens sobre TCP (usado pelos dois lados)
  pc/                   roda no PC ("o cérebro")
    server.py            servidor de texto; responde .upper() (regressão das etapas a/b)
    server_voz.py        etapa d: recebe áudio → transcreve → operador digita resposta
    stt.py               transcrição local com faster-whisper
  rasp/                 roda no Raspberry Pi (simulado no PC no início)
    client.py            cliente de texto (lê do teclado)
    audio_client.py      etapa d: grava do microfone → envia → imprime a resposta
    audio.py             captura de microfone (WAV 16 kHz mono)
    testar_microfone.py  diagnóstico de microfone
experiments/         protótipos fora do caminho atual (ex.: STT via API OpenAI)
PROGRESSO.md         log cronológico do que foi feito, decisões e próximos passos
```

## Como rodar

Sempre a partir de `src/` (isso põe `src/` no `sys.path`, então os
`import` entre pacotes funcionam sem gambiarra).

### Etapas (a) / (b) — só texto, sem dependências

```bash
cd src
python3 -m pc.server                    # PC: servidor (responde em MAIÚSCULAS)
python3 -m rasp.client 127.0.0.1 5000   # cliente (outro terminal, ou no Pi via túnel)
```

Detalhes dos dois modos (localhost e túnel SSH) em [docs/transporte.md](docs/transporte.md).

### Etapa (d) — áudio → texto → operador

Dependências:

```bash
pip install -r src/pc/requirements.txt     # no PC: faster-whisper
pip install -r src/rasp/requirements.txt   # no Pi: sounddevice, numpy
# no Pi real: sudo apt install -y libportaudio2
```

```bash
cd src
python3 -m pc.server_voz                     # PC: transcreve e espera a resposta do operador
python3 -m rasp.audio_client 127.0.0.1 5000  # Pi: Enter para gravar 5 s, envia, mostra a resposta
```

A primeira execução do `server_voz` baixa o modelo do faster-whisper
(`small` por padrão; `export LSA_WHISPER_MODEL=base` para um mais leve).

## Setup no Raspberry Pi

```bash
git clone https://github.com/Oliveira223/LSA_robot.git ~/dev/LSA_robot
```
