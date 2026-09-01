# LSA_robot — pipeline de comunicação Rasp ↔ PC

Robô que ouve áudio, manda para o PC, o PC processa (transcrição + IA) e devolve
texto; o Raspberry Pi converte esse texto em voz e toca num speaker.

O Raspberry Pi é só **"ouvido e boca"** (captura de microfone + reprodução no
speaker). Transcrição, IA e qualquer processamento pesado rodam **no PC**.

```
[Mic no Rasp] → rede → [PC: STT + IA] → texto → rede → [Rasp: TTS] → [Speaker]
```

## Estrutura

```
common/protocol.py   # enquadramento de mensagens sobre TCP (usado pelos dois lados)
notebook/            # código que roda no PC
  server.py          #   servidor TCP; hoje responde texto.upper() (placeholder STT+IA)
rasp/               # código que roda no Raspberry Pi
  client.py          #   cliente TCP interativo (lê do teclado, envia, imprime resposta)
00_NOTES/           # roadmaps e anotações de planejamento
PROGRESSO.md        # log cronológico do que foi feito, decisões e próximos passos
src/stl/            # peças 3D do robô
```

## Progressão (uma etapa por vez, cada uma testável)

- **(a)** dois terminais no mesmo PC (localhost), só texto — *feito*
- **(b)** mesmo código, via túnel SSH entre PC e Raspberry Pi
- **(c)** mesmo código, via WiFi direto (IP real na rede local)
- **(d)** integração de áudio (captura, envio, transcrição, TTS)

Detalhes e histórico em [PROGRESSO.md](PROGRESSO.md).

## Como rodar a etapa (a) — localhost

Requisito: Python 3 (sem dependências externas; só a biblioteca padrão).

```bash
# Terminal 1 — servidor
python3 notebook/server.py            # escuta em 127.0.0.1:5000

# Terminal 2 — cliente
python3 rasp/client.py                # conecta em 127.0.0.1:5000
```

Digite frases no cliente; devem voltar em MAIÚSCULAS. `Ctrl-C` encerra.
Host e porta são opcionais: `python3 rasp/client.py <host> <porta>`.

## Como rodar a etapa (b) — túnel SSH PC ↔ Raspberry Pi

```bash
# No PC, terminal 1
python3 notebook/server.py

# No PC, terminal 2 — abre shell no Pi + túnel reverso da porta 5000
ssh -R 5000:localhost:5000 <user>@<ip-do-pi>

# Já dentro do Pi (pela sessão acima)
cd ~/dev/LSA_robot
python3 rasp/client.py 127.0.0.1 5000
```

O tráfego do cliente no Pi entra em `127.0.0.1:5000` local e é encaminhado, por
dentro do SSH, até o `server.py` no PC.

## Setup no Raspberry Pi

```bash
git clone git@github.com:Oliveira223/LSA_robot.git ~/dev/LSA_robot
# ou, se o Pi não tiver chave SSH no GitHub:
# git clone https://github.com/Oliveira223/LSA_robot.git ~/dev/LSA_robot
```

Etapas (b) e (c) não exigem `pip install` — `client.py` usa só a biblioteca
padrão. Dependências de áudio (etapa d) entram depois, num virtualenv
(`python3 -m venv .venv`).
