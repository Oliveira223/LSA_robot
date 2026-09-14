# LSA_robot — pipeline de comunicação Jetson ↔ PC

Robô que ouve áudio, manda para o PC, o PC processa (transcrição + resposta)
e devolve texto; a Jetson converte esse texto em voz e toca num speaker.

A Jetson é só **"ouvido e boca"** (captura de microfone + reprodução no
speaker). Transcrição, "cérebro" e qualquer processamento pesado rodam **no PC**.

```
[Mic na Jetson] → rede → [PC: STT + resposta] → texto → rede → [Jetson: TTS] → [Speaker]
```

> O robô trocou de placa: era um Raspberry Pi, agora é uma **Jetson**. A
> Jetson já tem microfone e alto-falante reais (PrimeSense + HDMI — ver
> [docs/roadmap-comunicacao.md](docs/roadmap-comunicacao.md) e o histórico
> em [PROGRESSO.md](PROGRESSO.md)) e fala a resposta localmente com Piper.
> Ver "Chat por voz ao vivo" mais abaixo pra rodar o fluxo completo de
> conversa; as etapas (a)–(d2) abaixo continuam valendo como progressão
> histórica testável, uma peça de cada vez.

## Progressão (uma etapa por vez, cada uma testável)

| Etapa | O que valida | Status |
|---|---|---|
| **(a)** | dois terminais no mesmo PC (localhost), só texto | ✅ feito |
| **(b)** | mesmo código, via túnel SSH entre PC e Jetson | ✅ feito |
| **(c)** | mesmo código, via WiFi direto (IP real na LAN) | ⏸️ adiada (rede da PUC — ver [docs/transporte.md](docs/transporte.md)) |
| **(d1)** | "chat" de texto Jetson ↔ PC, sem áudio (sem mic na Jetson ainda) | ✅ feito |
| **(d2)** | integração de áudio (captura → transcrição → resposta → TTS) | ✅ feito, ao vivo Jetson↔PC (ver "Chat por voz ao vivo" abaixo — evoluiu do teste original em localhost) |

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
  pc/                   roda no PC/notebook ("o cérebro")
    server.py            servidor de texto; responde .upper() (regressão das etapas a/b)
    server_chat.py        etapa d1: recebe texto → operador digita resposta (sem áudio)
    server_voz.py         chat por voz ao vivo: recebe áudio da Jetson → transcreve →
                           operador responde → devolve texto (a Jetson fala com Piper)
    cerebro.py            a resposta em si (hoje: operador humano); usado por server_chat e server_voz
    stt.py                transcrição local com faster-whisper
    tts.py                síntese de voz local com espeak-ng — sem uso pelo server_voz hoje
                           (a voz de saída é da Jetson/Piper); fica pronto se precisar no futuro
    voice_client.py       caminho alternativo (mic do NOTEBOOK, não o da Jetson) — parado,
                           não usado no fluxo atual, mantido pra caso precise mais adiante
    push_to_talk.py       ferramenta local de teste de mic + transcrição, sem rede
    bin/server-voz        atalho: ativa o venv e roda pc.server_voz com os parâmetros certos
  jetson/               roda na Jetson (simulado no PC no início)
    client.py            cliente de texto (lê do teclado) — etapas a/b/d1
    audio_client.py      cliente antigo de teclado p/ etapa (d2) por duração fixa — obsoleto
                          desde o chat_client.py (VAD contínuo); não mexer, não interessa mais
    audio.py             captura de microfone por duração fixa (WAV 16 kHz mono)
    testar_microfone.py  diagnóstico de microfone
    mic_vad.py           escuta contínua com detecção de fala por volume (VAD) — usado pelo
                          chat por voz ao vivo e pelo gráfico de onda da câmera
    chat_client.py        conecta o mic da Jetson ao pc.server_voz; fala a resposta com Piper
    tts_server.py         servidor do caminho alternativo (mic do PC, porta 5001) — parado,
                           usado só se voice_client.py (acima) voltar a ser usado
    bin/camera-simples    atalho: mata processo antigo, reseta USB e reabre a tela da câmera
    bin/say, bin/voz       TTS local (Piper) — ver docs/roadmap-comunicacao.md
experiments/
  camera_prime_sense/Camera_Simples.py   painel da câmera: rosto, gráfico de onda do mic
                                          (fundo transparente, picos reais) e chat ao vivo
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

> Isso acima é o teste histórico, tudo em localhost. Pra conversar de
> verdade com a Jetson real (mic dela, câmera na tela, resposta falada por
> ela com Piper), ver "Chat por voz ao vivo" logo abaixo — é o fluxo atual.

### Chat por voz ao vivo (mic da Jetson + câmera + Piper)

Fluxo em uso hoje, dois processos em duas máquinas diferentes:

```
[mic da Jetson, VAD contínuo] → rede → [notebook: pc.server_voz
  transcreve + operador digita a resposta] → texto → rede →
  [Jetson: fala a resposta com Piper, mostra tudo na tela da câmera]
```

`jetson/chat_client.py` (dentro do painel da câmera,
`experiments/camera_prime_sense/Camera_Simples.py`) escuta o mic sozinho
(sem apertar nada), manda a frase pro `pc.server_voz` e fala a resposta
localmente — não depende de áudio vindo do PC. O `pc/voice_client.py`
(mic do notebook) é um caminho alternativo parado, não usado nesse fluxo
(ver árvore de arquivos acima).

#### 1. No notebook — servidor

```bash
cd ~/dev/repositories/LSA_robot     # ou onde estiver seu checkout
python3 -m venv .venv && . .venv/bin/activate   # só na primeira vez
pip install -r src/pc/requirements.txt          # só na primeira vez

src/pc/bin/server-voz                # escuta em 0.0.0.0:5000 (padrão)
# ou, pra trocar a porta:
src/pc/bin/server-voz 5050
```

Espera aparecer:
```
server-voz: escutando em 0.0.0.0:5000 (Ctrl-C para sair)
[servidor] modo voz: transcreve o audio, voce digita a resposta; a Jetson fala
```

A primeira transcrição demora mais (baixa/carrega o modelo do
faster-whisper). Pra acelerar: `export LSA_WHISPER_MODEL=base` (ou `tiny`)
antes de rodar o `server-voz`.

#### 2. Na Jetson, via SSH — câmera + chat

Precisa do `DISPLAY`/`XAUTHORITY` da sessão gráfica que já está rodando na
tela física (a janela não abre "dentro" do SSH) e do IP/hostname do
notebook (pra Jetson saber pra onde mandar o áudio):

```bash
ssh lsa-robot@<ip-da-jetson>

DISPLAY=:0 camera-simples --chat-host <ip-ou-hostname-do-notebook> --chat-port 5000
```

`<ip-ou-hostname-do-notebook>` — no mDNS da rede local costuma funcionar
`<hostname>.local` (ex.: `pop-os.local`); se não resolver, usa o IP mesmo
(`hostname -I` no notebook mostra o dele).

Parâmetros úteis do `camera-simples` (repassados direto pro
`Camera_Simples.py`):

| Parâmetro | Efeito |
|---|---|
| `--chat-host <host>` | IP/hostname do notebook rodando `pc.server_voz` (obrigatório pra sair do padrão `127.0.0.1`) |
| `--chat-port <porta>` | porta do `pc.server_voz` (padrão 5000) |
| `--no-chat` | só o indicador de mic, sem conectar no notebook |
| `--no-mic` | desliga também o gráfico de onda do mic |
| `--no-faces` | desliga a detecção de rosto (economiza CPU) |
| `--corner tl\|tr\|bl\|br` | canto onde o gráfico de onda aparece |
| `--windowed` | janela normal em vez de tela cheia (útil testando) |

Outros comandos do `camera-simples`:
```bash
DISPLAY=:0 camera-simples status   # mostra se está rodando
DISPLAY=:0 camera-simples stop     # fecha e libera o sensor/mic
```

`DISPLAY=:0` funciona na maioria dos casos; se não abrir, `ls
/tmp/.X11-unix/` mostra o número do display ativo de verdade (`X0` → `:0`,
`X1` → `:1`, etc.) — pode mudar depois de um reboot.

**Rodando na mão, sem o `camera-simples`** (útil pra ver o log completo ao
vivo, ou mudar parâmetros rápido sem editar script):
```bash
export DISPLAY=:0
export XAUTHORITY=~/.Xauthority
cd ~/dev/Camera-prime-sense                    # o app RODANDO fica fora do repo — ver nota abaixo
./venv/bin/python3 -u Camera_Simples.py --chat-host pop-os.local --chat-port 5000
```
`Ctrl-C` no terminal (ou `q`/`ESC` na janela) fecha.

> **Nota:** `experiments/camera_prime_sense/Camera_Simples.py`, no repo, é
> o código-fonte; quem roda de verdade é a cópia em
> `~/dev/Camera-prime-sense` (fora do repo, com o venv e os pesos do
> modelo YOLO — grandes demais pro git). As duas cópias não se sincronizam
> sozinhas: depois de editar uma, copie pra outra. Detalhes em
> `~/Desktop/README.md` (na própria Jetson).

#### Se o mic da Jetson não abrir

A PrimeSense é conhecida por travar a interface de áudio USB às vezes
(mais comum logo depois de outro programa segurar o sensor, ou depois de
muito tempo ligada). Sintoma: o log mostra `arecord: Unable to install hw
params` repetidamente e desiste depois de algumas tentativas ("mic
offline" no painel da câmera). `camera-simples` já tenta um reset USB por
software antes de reabrir; se mesmo assim não resolver, é preciso
desconectar e reconectar fisicamente o cabo USB da PrimeSense — não tem
jeito por software pra esse caso.

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
