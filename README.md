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

Dependências (cada máquina só instala o que roda nela):

```bash
# PC — faster-whisper. Se o pip reclamar de "externally-managed", use venv:
python3 -m venv .venv && . .venv/bin/activate
pip install -r src/pc/requirements.txt

# Raspberry Pi — no Raspberry Pi OS (PEP 668) o pip no Python do sistema é
# bloqueado, e python3-sounddevice não está no apt. numpy vem do apt;
# sounddevice (pequeno, puro Python) entra num venv que enxerga o sistema:
sudo apt install -y python3-numpy libportaudio2
python3 -m venv --system-site-packages ~/dev/LSA_robot/.venv
~/dev/LSA_robot/.venv/bin/pip install sounddevice
#   rodar com: ~/dev/LSA_robot/.venv/bin/python -m rasp.audio_client ...
#   (libportaudio2 é obrigatório — é a lib C que o sounddevice usa em runtime)
```

Enquanto o Pi está simulado no próprio notebook, essa máquina faz os dois
papéis e precisa dos dois conjuntos.

```bash
cd src
python3 -m pc.server_voz                     # PC: transcreve e espera a resposta do operador
python3 -m rasp.audio_client 127.0.0.1 5000  # Pi: Enter para gravar 5 s, envia, mostra a resposta
```

A primeira execução do `server_voz` baixa o modelo do faster-whisper
(`small` por padrão; `export LSA_WHISPER_MODEL=base` — ou `tiny` — para um
mais leve, útil em conexão ruim).

### Ferramenta local — captura + transcrição

Teste de microfone + transcrição numa máquina só, sem rede. Fica em loop:
**ENTER** começa a gravar, **ENTER** de novo para, a frase transcrita aparece.
`q` + ENTER (ou Ctrl-C) sai.

```bash
cd src
python3 -m pc.push_to_talk            # microfone padrão
python3 -m pc.push_to_talk 0          # forçar um índice de mic
python3 -m pc.push_to_talk --list     # listar entradas de áudio
```

Precisa de `sounddevice`, `numpy` e `faster-whisper` (`src/pc/requirements.txt`).
Lê o teclado do stdin normal do terminal — funciona em qualquer terminal,
X11 ou Wayland, e por SSH.

## Setup no Raspberry Pi

```bash
git clone https://github.com/Oliveira223/LSA_robot.git ~/dev/LSA_robot
```
