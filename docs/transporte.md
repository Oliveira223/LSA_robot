# Transporte — camada de mensagens Jetson ↔ PC

Referência estável da parte que **já funciona e foi validada**: o canal
TCP que leva texto (e, a partir da etapa d, áudio) entre a Jetson e
o PC. O histórico datado de como se chegou aqui está em
[`../PROGRESSO.md`](../PROGRESSO.md); este documento descreve o estado atual.

## Formato de fio

TCP é um fluxo de bytes sem fronteira de mensagem: um `recv()` pode trazer
meia mensagem, uma inteira ou várias grudadas. Cada mensagem vai enquadrada:

```
[ 1 byte: tipo ] [ 4 bytes: tamanho do payload, big-endian, sem sinal ] [ payload ]

tipo 0x01 = TEXTO  → payload em UTF-8
tipo 0x02 = AUDIO  → payload = bytes de um arquivo WAV
```

- **Por que prefixo de tamanho e não delimitador (`\n`):** o payload pode
  conter qualquer byte (inclusive `\n`, e binário de áudio). Saber o
  tamanho de antemão é o que garante ler a mensagem inteira, picada em
  quantos `recv()` forem necessários.
- **Por que o byte de tipo:** até a etapa (b) só trafegava texto. A etapa
  (d) manda WAV pelo mesmo socket, e o receptor precisa saber o que chegou.
- **Teto:** `MAX_MSG = 64 MiB` por mensagem (sanidade; cobre um WAV curto).

Implementação: [`../src/common/protocol.py`](../src/common/protocol.py).

## API

| Função | Uso |
|---|---|
| `send_texto(sock, s: str)` | envia `s` como mensagem TEXTO |
| `send_audio(sock, dados: bytes)` | envia `dados` (WAV) como mensagem AUDIO |
| `recv_msg(sock) -> Mensagem \| None` | lê uma mensagem; `None` se o outro lado fechou limpo |

`Mensagem` é um `namedtuple(tipo, dados)`. `.texto` decodifica `dados` como
UTF-8 (só para `tipo == TEXTO`; erro caso contrário). `recv_msg` levanta
`ValueError` se o cabeçalho anunciar tipo desconhecido ou tamanho absurdo
(sinal de corrupção ou de protocolos diferentes nas duas pontas).

## Modo 1 — localhost (etapa a)

Sem dependências externas (só biblioteca padrão). A partir de `src/`:

```bash
# Terminal 1 — servidor de texto (responde em MAIÚSCULAS)
python3 -m pc.server                 # escuta em 127.0.0.1:5000

# Terminal 2 — cliente de texto
python3 -m jetson.client             # conecta em 127.0.0.1:5000
```

Host e porta são opcionais: `python3 -m jetson.client <host> <porta>`.

Para um "chat" de verdade (operador do PC digita a resposta em vez de
`.upper()`), troque `pc.server` por `pc.server_chat` — ver
[`../README.md`](../README.md#etapa-d1--chat-de-texto-jetson--pc-sem-áudio-sem-dependências).

## Modo 2 — túnel SSH reverso PC ↔ Jetson (etapa b)

O SSH já funciona no sentido PC → Jetson. O túnel reverso (`-R`) reaproveita
essa conexão: a Jetson passa a escutar em `127.0.0.1:5000` e encaminha, por
dentro do SSH, para o `127.0.0.1:5000` do PC.

```bash
# No PC, terminal 1
python3 -m pc.server

# No PC, terminal 2 — abre shell na Jetson + túnel reverso da porta 5000
ssh -R 5000:localhost:5000 <user>@<ip-da-jetson>

# Já dentro da Jetson (pela sessão acima)
cd ~/dev/LSA_robot/src
python3 -m jetson.client 127.0.0.1 5000
```

Nenhuma mudança de código entre os modos — só o destino do socket.

## O que foi validado

Nos modos 1 e 2, com `pc.server` (eco `.upper()`):

- ida e volta simples;
- texto com acento (cedilha, til) preservado nos dois sentidos;
- mensagem vazia → resposta vazia, sem travar;
- payload longo (~5000 caracteres) → volta íntegro (enquadramento por tamanho ok);
- fechar e reabrir o cliente sem derrubar o servidor (`SO_REUSEADDR` + `listen(1)`).

Observação da etapa (b): a primeira tentativa de `ssh` falhou com
`No route to host` porque a Jetson trocou de IP via DHCP (isso foi na época
em que a placa ainda era um Raspberry Pi; a observação vale igual na
Jetson). Reservar IP fixo para a Jetson no roteador resolve — e é
pré-requisito da etapa (c).

## Etapa (c) — WiFi direto: adiada

TCP peer-to-peer direto na LAN (sem túnel) está adiado: o desenvolvimento
acontece na rede da PUC, que tem isolamento de clientes (dispositivos no
mesmo WiFi não se enxergam) e não dá acesso à config do roteador. O túnel
SSH da etapa (b) é o transporte em uso. Como o código é agnóstico de
transporte, retomar a (c) numa rede doméstica é só trocar o IP do argumento.
