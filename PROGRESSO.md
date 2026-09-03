# PROGRESSO — Pipeline de comunicação Rasp ↔ PC

Log cronológico do projeto de comunicação (áudio → PC → texto → voz).
Cada entrada: data, o que foi tentado, o que funcionou / não funcionou,
decisões importantes, próximo passo.

Ordem de progressão planejada:
- **(a)** dois terminais no mesmo notebook (localhost), só texto, via socket
- **(b)** mesmo código, via túnel SSH entre notebook e Raspberry Pi
- **(c)** mesmo código, via WiFi direto (IP real na rede local)
  — _adiada: rede da PUC tem isolamento de clientes; usando o túnel SSH da (b)_
- **(d)** integração de áudio (captura, envio, transcrição, TTS)

> Nota: os caminhos citados nas entradas antigas (`common/`, `notebook/`,
> `rasp/`) valiam na data delas. Desde **2026-09-03** o código está sob
> `src/{common,pc,rasp}/` — ver a entrada dessa data e
> [`docs/transporte.md`](docs/transporte.md) para os caminhos atuais.

---

## 2026-09-01

### Etapa (a) — Comunicação de texto via socket no mesmo notebook (localhost)

**O que foi tentado**
- Estrutura criada dentro de `LSA_robot/`:
  - `common/protocol.py` — enquadramento de mensagens sobre TCP:
    4 bytes big-endian com o tamanho do payload + payload UTF-8.
    Funções `send_msg(sock, texto)` e `recv_msg(sock) -> str | None`.
  - `notebook/server.py` — servidor TCP, escuta em `127.0.0.1:5000`,
    um cliente por vez, responde `texto.upper()` (placeholder da futura
    camada STT + IA). Host/porta por argv.
  - `rasp/client.py` — cliente TCP interativo, lê do teclado, envia,
    imprime a resposta. Host/porta por argv.

**O que funcionou / não funcionou**
- ✅ Teste automatizado (`scratchpad/smoke.py`): servidor num processo,
  cliente com entrada canalizada. Todas as respostas voltaram corretas:
  - `oi robo` → `OI ROBO`
  - `comunicação` (com cedilha e til) → `COMUNICAÇÃO` — acento preservado
  - mensagem vazia → resposta vazia, sem travar
  - payload de 5000 caracteres → voltou íntegro (enquadramento por
    tamanho funcionando; nada de mensagem cortada)
  - segundo cliente conectou depois do primeiro sair → servidor voltou a
    aceitar conexão sem reiniciar
- Ambiente: Python 3.12.3 no PC.
- Pendente: teste manual do próprio usuário nos dois terminais + Ctrl-C.

**Decisões importantes**
- **TCP em vez de UDP:** o canal transporta texto (transcrição, resposta
  da IA) e, mais adiante, arquivos de áudio. Não pode perder nem reordenar
  dados. UDP só será cogitado para streaming de áudio bruto na fase
  avançada, se latência virar mais crítica que garantia de entrega.
- **Enquadramento por prefixo de tamanho (e não por `\n`):** TCP é um
  fluxo de bytes sem fronteira de mensagem; um `recv()` pode trazer
  pedaço de mensagem ou várias juntas. Mandar o tamanho antes dos dados
  é a mesma técnica que será usada para enviar áudio depois, então a
  função é reaproveitada em todas as fases.
- **Localhost antes de SSH/WiFi:** isola a lógica de socket de qualquer
  problema de rede/firewall. Nas etapas (b) e (c) o código não muda —
  só o IP passado como argumento.
- **Host/porta como argumentos de linha de comando** (default
  `127.0.0.1 5000`), para não editar código nas próximas etapas.

**Próximo passo planejado**
- Rodar `notebook/server.py` e `rasp/client.py` em dois terminais.
- Validar: ida e volta simples, texto com acento, mensagem vazia,
  mensagem longa, e fechar/reabrir o cliente sem derrubar o servidor.
- Se tudo OK: etapa (b) — mesmo código via túnel SSH notebook ↔ Rasp.

### Preparação do Raspberry Pi (para a etapa b)

**O que foi tentado**
- Acesso SSH ao Pi: `ssh admin@192.168.0.102` (login por senha, chave
  ainda não configurada).
- Corrigir aviso de locale: `sudo apt-get install -y locales-all`.

**O que funcionou / não funcionou**
- ✅ SSH conecta. Pi = Raspberry Pi OS trixie, kernel 6.18, aarch64.
- ⚠️ Durante o `apt install locales-all` o SSH caiu (`Broken pipe`) e o
  `dpkg` ficou meio-instalado. Recuperado com `sudo dpkg --configure -a`;
  `dpkg -l locales-all` depois mostrou `ii` (ok).
- ✅ Após reconectar, `locale` roda sem erro. Ambiente ainda mistura
  `LANG=en_GB.UTF-8` com `LC_*=pt_BR.UTF-8` (ambos UTF-8, inofensivo;
  limpeza opcional com `sudo update-locale`).
- Ambiente do Pi: **Python 3.13.5**, git 2.47.3, IP atual `192.168.0.102`
  (via DHCP — falta reserva/IP fixo). `tmux` não instalado.

**Decisões importantes**
- **Transporte de código PC → Pi: `git`** (revisto). Adicionados
  `.gitignore` e `README.md` na raiz; `notes/` virou `00_NOTES/`.
  Commit "Etapa (a) ..." + push; `git clone` no Pi em `~/dev/LSA_robot`
  funcionou (repo inteiro, incluindo `src/stl` — inofensivo).
- Comandos longos no Pi devem rodar dentro de `tmux` para sobreviver a
  quedas do SSH (aprendido na queda durante o `apt install`).

---

### Etapa (b) — Mesmo código via túnel SSH PC ↔ Raspberry Pi  [validada]

**Plano**
- `notebook/server.py` roda no PC, escutando em `127.0.0.1:5000`.
- Do PC: `ssh -R 5000:localhost:5000 admin@192.168.0.102` — túnel reverso:
  o Pi passa a escutar em `127.0.0.1:5000` e encaminha, por dentro do SSH,
  para o `127.0.0.1:5000` do PC.
- No Pi (pela sessão SSH acima): `python3 rasp/client.py 127.0.0.1 5000`.
- Sem mudança de código — `client.py` já aceita host/porta por argv.

**Por que túnel reverso (`-R`) e não `-L`:** o SSH já funciona no sentido
PC → Pi. `-L` exigiria o Pi conectar por SSH no PC (PC precisaria de sshd
rodando e acessível). `-R` reaproveita a conexão que já existe.

**Por que esta etapa antes do WiFi direto (c):** isola "o código de
socket funciona entre duas máquinas?" de "consigo TCP direto pela rede?"
(firewall, bind em `0.0.0.0`, descoberta de IP). Se (b) passa e (c)
falha, o problema é config de rede, não o código.

**O que funcionou / não funcionou**
- ✅ Túnel reverso SSH (`ssh -R 5000:localhost:5000 admin@192.168.0.102`)
  estabelecido do PC. `notebook/server.py` rodando no PC em `127.0.0.1:5000`.
- ✅ `rasp/client.py 127.0.0.1 5000` rodando no Pi por dentro do túnel,
  sem mudança de código. Validado entre as duas máquinas:
  - `ola` / `OLA` → ida e volta simples ok
  - `olá` → `OLÁ` — acento preservado na ida e na volta
  - mensagem vazia (2x) → resposta vazia, sem travar
  - payload longo de `aaaa…` → voltou `AAAA…` íntegro (enquadramento ok)
  - `@@@@@` → `@@@@@` — caracteres especiais ok
- ⏳ Ainda não testado nesta rodada: fechar e reabrir o `client.py` no Pi
  sem derrubar o servidor (já validado em localhost na etapa (a)).
- Nota: primeira tentativa de `ssh` falhou com `No route to host` (Pi tinha
  pego outro IP via DHCP); conectou no IP correto na sequência. Reforça a
  pendência de reserva de DHCP / IP fixo para o Pi antes da etapa (c).

**Próximo passo planejado**
- (Opcional) confirmar reconexão do cliente entre máquinas.
- Etapa (c) — WiFi direto: `notebook/server.py` com bind em `0.0.0.0:5000`
  no PC; `rasp/client.py <IP_DO_PC_NA_LAN> 5000` no Pi, sem túnel SSH.
  Antes disso: reservar IP fixo para o Pi no roteador.

---

### Etapa (c) — WiFi direto (TCP na LAN, sem túnel SSH)  [adiada]

**Por que adiada:** o desenvolvimento está sendo feito na rede da PUC, que
tem isolamento de clientes (dispositivos no mesmo WiFi não se enxergam) e
não dá acesso à config do roteador (sem reserva de DHCP / IP fixo para o
Pi). TCP peer-to-peer direto não é viável nesse ambiente.

**Contorno em uso:** a etapa (b) (túnel SSH reverso) continua sendo o
transporte padrão. O `ssh -R` passa por cima do isolamento de rede porque
tudo trafega dentro da conexão SSH que já funciona PC → Pi. Como o código
é agnóstico de transporte (só recebe host/porta por argv), retomar a (c)
mais tarde numa rede doméstica é só trocar o argumento — nenhuma mudança
de código pendente por causa disso.

**Plano (quando retomar, em rede sob controle)**
- `notebook/server.py` no PC com bind em `0.0.0.0:5000` (hoje só aceita
  `127.0.0.1`).
- `rasp/client.py <IP_DO_PC_NA_LAN> 5000` no Pi, sem `ssh -R` no meio.
- Antes: reservar IP fixo para o Pi no roteador.
- Validar as mesmas variações da etapa (b). Se falhar mas (b) passou:
  problema é rede (firewall do PC / `ufw` / porta 5000), não o código.

**Próximo passo:** seguir para a etapa (d) — integração de áudio — usando
o túnel SSH da etapa (b) como transporte.

---

## 2026-09-03

### Reorganização do repositório

**O que foi feito**
- Código executável concentrado em `src/`, separado por papel:
  `src/common/protocol.py`, `src/pc/` (o cérebro), `src/rasp/` (ouvido e boca).
  Fim das gambiarras de `sys.path` — roda a partir de `src/` via `python3 -m`
  (ex.: `python3 -m pc.server`, `python3 -m rasp.client`).
- `notebook/` → `src/pc/`; `rasp/` → `src/rasp/`; `common/` → `src/common/`.
- Peças 3D saíram de `src/stl/` para `hardware/stl/` (`face-and-jaw/`, `neck/`),
  com README próprio; `.stl` marcado como binário no `.gitattributes` da raiz.
- Roadmaps e to-do de `00_NOTES/` para `docs/`. Novo `docs/transporte.md`:
  referência estável da camada de texto (etapas a/b) que já funciona.
- `CODIGO_RASP/` desmembrado: `audio.py` (captura de mic) e `testar_microfone.py`
  foram para `src/rasp/`; a parte de STT via API OpenAI foi para
  `experiments/audio_openai/` (fora do caminho atual, guardada como referência).
  Removidos do versionamento: `teste_python.wav`, `__pycache__/`, `gitignore.txt`.
- `.gitignore`: + `.env`, `*.key`, `audio_temp/`.

**Decisões importantes**
- **Estrutura por papel, não por etapa.** `pc/` e `rasp/` mapeiam as duas
  máquinas físicas — é estável. Etapas entram e saem; o que está pronto vs.
  em desenvolvimento fica marcado aqui e no `README.md`, não na árvore de pastas.
- **`experiments/` para spikes.** Código funcional mas fora do caminho atual
  fica visível e separado, sem poluir `src/`.

### Etapa (d) — integração de áudio  [em desenvolvimento]

**Plano desta rodada:** áudio → texto → operador humano (sem IA ainda).
1. Pi grava 5 s do microfone e envia o WAV pelo socket.
2. PC transcreve com `faster-whisper` local (offline, sem chave, sem custo).
3. PC mostra a transcrição; um **operador humano** digita a resposta.
4. Resposta volta como texto para o Pi (TTS entra numa sub-etapa depois).
5. Mais adiante o operador é trocado pela IA de fato (regras + modelo).

**O que foi feito**
- `src/common/protocol.py` estendido: enquadramento agora é
  `[1 byte tipo][4 bytes tamanho][payload]`, com `tipo` TEXTO (0x01) ou
  AUDIO (0x02). Novas funções `send_texto` / `send_audio` / `recv_msg → Mensagem`.
  `server.py` e `client.py` (texto) atualizados para a API nova.
- `src/pc/stt.py` — wrapper do `faster-whisper`. Modelo `small` por padrão
  (`export LSA_WHISPER_MODEL=base` para um mais leve); carregado 1x por processo.
- `src/pc/server_voz.py` — servidor da etapa (d): recebe AUDIO, transcreve,
  imprime `[ouvido] ...`, lê `[operador] resposta>`, devolve como TEXTO.
  `server.py` (eco `.upper()`) fica como regressão das etapas (a)/(b).
- `src/rasp/audio_client.py` — grava (reusa `audio.py`), pula se silencioso,
  envia o WAV, imprime `robo> ...`.
- `requirements` separados: `src/pc/` (`faster-whisper`), `src/rasp/`
  (`sounddevice`, `numpy`; no Pi real ainda `sudo apt install libportaudio2`).

**O que funcionou / não funcionou**
- ✅ Regressão do transporte de texto com o protocolo novo (byte de tipo),
  em localhost: `oi robo`→`OI ROBO`, `comunicação`→`COMUNICAÇÃO` (acento),
  linha vazia→vazia, ~5000 chars→íntegro, `@@@@@`→`@@@@@`, e segundo cliente
  conecta depois do primeiro sair sem reiniciar o servidor.
- ✅ Round-trip do protocolo em socketpair: AUDIO de ~100 KB binário volta
  idêntico; `.texto` em mensagem AUDIO levanta erro; fechamento limpo → `None`.
- ✅ `import` de `common.protocol`, `pc.server`, `pc.server_voz`, `pc.stt`,
  `rasp.client` a partir de `src/` (deps de `faster-whisper`/`sounddevice`
  são carregadas só quando usadas).
- ⏳ Não testado neste ambiente (faltam libs/hardware): `faster-whisper` de
  verdade transcrevendo um WAV, captura de microfone, e o loop completo
  `server_voz` ↔ `audio_client`. São os itens 2–4 da Verificação do plano.

**Próximo passo planejado**
- Rodar aqui: instalar `src/pc/requirements.txt`, gravar um WAV com
  `python3 -m rasp.testar_microfone`, transcrever com `python3 -m pc.stt`,
  e o loop completo `server_voz` ↔ `audio_client` em localhost; depois pelo
  túnel SSH com o Pi.
- Sub-etapas seguintes: TTS da resposta no Pi (`espeak-ng` ou `piper`);
  trocar o operador humano por regras + IA.
