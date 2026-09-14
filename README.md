# LSA_robot — pipeline de comunicação Jetson ↔ PC

Robô que ouve áudio, manda para o PC, o PC processa (transcrição + resposta)
e devolve texto; a Jetson converte esse texto em voz e toca num speaker.

A Jetson é só **"ouvido e boca"** (captura de microfone + reprodução no
speaker). Transcrição, "cérebro" e qualquer processamento pesado rodam **no PC**.

```
[Mic na Jetson] → rede → [PC: STT + resposta] → texto → rede → [Jetson: TTS] → [Speaker]
```

> O robô trocou de placa: era um Raspberry Pi, agora é uma **Jetson**. A
> Jetson ainda não tem microfone nem alto-falante plugados, então por
> enquanto ela só troca **texto** com o PC pelo teclado/terminal — ver
> etapa (d1) abaixo. Assim que o áudio chegar, o mesmo protocolo passa a
> carregar voz (etapa d2), sem mudar o cliente da Jetson.

## Progressão (uma etapa por vez, cada uma testável)

| Etapa | O que valida | Status |
|---|---|---|
| **(a)** | dois terminais no mesmo PC (localhost), só texto | ✅ feito |
| **(b)** | mesmo código, via túnel SSH entre PC e Jetson | ✅ feito |
| **(c)** | mesmo código, via WiFi direto (IP real na LAN) | ⏸️ adiada (rede da PUC — ver [docs/transporte.md](docs/transporte.md)) |
| **(d1)** | "chat" de texto Jetson ↔ PC, sem áudio (sem mic na Jetson ainda) | ✅ feito |
| **(d2)** | integração de áudio (captura → transcrição → resposta → TTS) | 🔜 em teste local no PC (localhost); Jetson ainda sem áudio |

Em (d1) e (d2), enquanto não há IA, um **operador humano** no PC lê a
mensagem (texto ou transcrição) e digita a resposta — fazendo as vezes do
"outro usuário" da conversa. Depois o operador é trocado pela IA. Histórico
e decisões em [PROGRESSO.md](PROGRESSO.md).

## Estrutura

```
docs/                roadmaps, to-do e a referência do transporte
  transporte.md        formato de fio + como rodar as etapas a/b (o que já funciona)
hardware/stl/         peças 3D do robô (InMoov, CC BY-NC) — sem código
src/                  todo o código executável; rode a partir daqui, via python3 -m
  common/protocol.py    enquadramento de mensagens sobre TCP (usado pelos dois lados)
  common/audio_io.py    captura (ENTER/ENTER) e reprodução de WAV; compartilhado pc ↔ jetson
  pc/                   roda no PC ("o cérebro")
    server.py            servidor de texto; responde .upper() (regressão das etapas a/b)
    server_chat.py        etapa d1: recebe texto → operador digita resposta (sem áudio)
    server_voz.py         etapa d2: recebe áudio → transcreve → operador responde → devolve voz
    cerebro.py            a resposta em si (hoje: operador humano); usado por server_chat e server_voz
    stt.py                transcrição local com faster-whisper
    tts.py                síntese de voz local com espeak-ng (resposta → WAV)
  jetson/               roda na Jetson (simulado no PC no início)
    client.py            cliente de texto (lê do teclado) — etapas a/b/d1
    audio_client.py      etapa d2: ENTER grava / ENTER para → envia → toca a resposta em voz
    audio.py             captura de microfone por duração fixa (WAV 16 kHz mono)
    testar_microfone.py  diagnóstico de microfone
experiments/         protótipos fora do caminho atual (ex.: STT via API OpenAI)
PROGRESSO.md         log cronológico do que foi feito, decisões e próximos passos
```

## Como rodar

Sempre a partir de `src/` (isso põe `src/` no `sys.path`, então os
`import` entre pacotes funcionam sem gambiarra).

### Etapas (a) / (b) — eco `.upper()`, só texto, sem dependências

```bash
cd src
python3 -m pc.server                      # PC: servidor (responde em MAIÚSCULAS)
python3 -m jetson.client 127.0.0.1 5000   # cliente (outro terminal, ou na Jetson via túnel)
```

Detalhes dos dois modos (localhost e túnel SSH) em [docs/transporte.md](docs/transporte.md).

### Etapa (d1) — "chat" de texto Jetson ↔ PC (sem áudio, sem dependências)

Enquanto a Jetson não tem microfone: alguém escreve uma mensagem no
terminal da Jetson, ela chega ao PC, e o operador do PC digita a resposta
de volta — como um app simples de troca de mensagens.

```bash
cd src
python3 -m pc.server_chat                     # PC: espera voce digitar a resposta de cada mensagem
python3 -m jetson.client 127.0.0.1 5000       # Jetson: escreve e le a resposta
```

Sem dependências externas (só biblioteca padrão) — igual às etapas (a)/(b).
Isso já deixa o máximo do trabalho no PC (`pc.cerebro.responder`), pronto
para virar IA de verdade sem mexer no cliente da Jetson (ver
[docs/roadmap-ia-conversacional.md](docs/roadmap-ia-conversacional.md)).

### Etapa (d2) — áudio → transcrição → operador → voz

Fala captada pelo microfone → WAV pelo socket → `faster-whisper` transcreve
no PC → operador digita a resposta → `espeak-ng` sintetiza a resposta no PC
→ WAV volta pelo socket → toca no speaker. A Jetson só grava e toca; a voz
é gerada no PC (ver [docs/roadmap-comunicacao.md](docs/roadmap-comunicacao.md), Fase 4).

Enquanto a Jetson não tem microfone/speaker, o teste roda **todo no PC**
(localhost): o notebook grava pelo próprio mic e toca pelos próprios
alto-falantes.

Dependências:

```bash
# PC — faster-whisper (pip) + espeak-ng (apt). Se o pip reclamar de
# "externally-managed", use venv:
python3 -m venv .venv && . .venv/bin/activate
pip install -r src/pc/requirements.txt
sudo apt install -y espeak-ng          # TTS; sem isso a resposta volta como texto

# Também precisa de sounddevice + numpy para a captura/reprodução (já estão
# no requirements do PC). Na Jetson de verdade, mais adiante:
#   sudo apt install -y libportaudio2   # lib C do sounddevice em runtime
```

Rodar (dois terminais no PC, a partir de `src/`):

```bash
cd src
python3 -m pc.server_voz                        # transcreve, você digita a resposta, ela volta em voz
python3 -m jetson.audio_client 127.0.0.1 5000   # ENTER grava / ENTER para / q sai
#   3º argumento opcional: índice do microfone (veja 'python3 -m pc.push_to_talk --list')
```

A primeira execução do `server_voz` baixa o modelo do faster-whisper
(`small` por padrão; `export LSA_WHISPER_MODEL=base` — ou `tiny` — para um
mais leve, útil em conexão ruim). Voz e ritmo do TTS: `export LSA_TTS_VOZ=pt`,
`export LSA_TTS_WPM=175`.

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

## Setup na Jetson

```bash
git clone https://github.com/Oliveira223/LSA_robot.git ~/dev/LSA_robot
```
