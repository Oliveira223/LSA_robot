# PROGRESSO — Pipeline de comunicação Jetson ↔ PC

Log cronológico do projeto de comunicação (áudio → PC → texto → voz).
Cada entrada: data, o que foi tentado, o que funcionou / não funcionou,
decisões importantes, próximo passo.

Ordem de progressão planejada:
- **(a)** dois terminais no mesmo notebook (localhost), só texto, via socket
- **(b)** mesmo código, via túnel SSH entre notebook e a placa do robô
- **(c)** mesmo código, via WiFi direto (IP real na rede local)
  — _adiada: rede da PUC tem isolamento de clientes; usando o túnel SSH da (b)_
- **(d)** integração de áudio (captura, envio, transcrição, TTS)
  — _dividida em (d1) chat de texto e (d2) áudio de verdade, ver entrada de 2026-09-04_

> Nota: os caminhos e nomes citados nas entradas antigas (`common/`,
> `notebook/`, `rasp/`) valiam na data delas. Desde **2026-09-03** o código
> está sob `src/{common,pc,rasp}/`; desde **2026-09-04** a placa do robô é
> uma **Jetson** (não mais um Raspberry Pi) e `src/rasp/` virou
> `src/jetson/` — ver a entrada de 2026-09-04 e
> [`docs/transporte.md`](docs/transporte.md) para os caminhos e nomes atuais.

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
- `requirements` separados: `src/pc/` (`faster-whisper`, + `sounddevice`,
  `numpy` para a ferramenta local abaixo), `src/rasp/` (`sounddevice`,
  `numpy`; no Pi real ainda `sudo apt install libportaudio2`).
- `src/pc/stt.py` ganhou `transcrever_array(amostras, taxa)` (sinal já em
  memória, com reamostragem linear p/ 16 kHz) além de `transcrever(caminho)`.
  Passou a forçar `HF_HUB_DISABLE_XET=1` por padrão — o backend "xet" da
  HuggingFace trava em rede que corta conexão longa (visto na PUC).
- `src/pc/push_to_talk.py` — ferramenta local (sem rede): ENTER grava, ENTER
  para, transcreve, imprime a frase + `dur · pico · stt Ns`, em loop;
  `q`/Ctrl-C sai. Um `Cronometro` (thread) mostra os segundos correndo na
  mesma linha durante a gravação e durante a transcrição. Normaliza áudio
  fraco antes do Whisper. Lê o stdin do terminal (sem `pynput`) — funciona
  em X11, Wayland e SSH (o notebook está em sessão Wayland).
- Tentativa de barra de progresso `%` descartada: o faster-whisper decodifica
  áudio de até 30 s numa janela só e só entrega os segmentos no fim — para
  clipes curtos o progresso pula de 0 % direto pro resultado. (O hook de
  callback ficou em `stt._consumir` para uso futuro com áudio longo.)
- `stt.transcribe` passou a usar `temperature=0`: sem a escada de "repete com
  temperatura maior" quando o modelo fica inseguro (fala curta/pouco clara
  chegava a 6-7× o tempo real). Latência mais previsível; `small` em CPU
  fica ~3-4× o tempo do áudio — se precisar mais rápido, `LSA_WHISPER_MODEL=base`.
- Idioma já fixo em `pt` no `transcribe` (pula a detecção automática).

**O que funcionou / não funcionou**
- ✅ Regressão do transporte de texto com o protocolo novo (byte de tipo),
  em localhost: `oi robo`→`OI ROBO`, `comunicação`→`COMUNICAÇÃO` (acento),
  linha vazia→vazia, ~5000 chars→íntegro, `@@@@@`→`@@@@@`, e segundo cliente
  conecta depois do primeiro sair sem reiniciar o servidor.
- ✅ Round-trip do protocolo em socketpair: AUDIO de ~100 KB binário volta
  idêntico; `.texto` em mensagem AUDIO levanta erro; fechamento limpo → `None`.
- ✅ venv no PC (Pop!_OS, PEP 668): `.venv` na raiz + `src/pc/requirements.txt`
  + `src/rasp/requirements.txt` + `pynput` + `sudo apt install libportaudio2`.
- ✅ Modelo `small` do faster-whisper baixou (a rede da PUC corta download
  longo — `xet` falhou; HTTPS com retomada do `.incomplete` completou em
  várias tentativas). Carrega e roda; `transcrever_array` + reamostragem
  44100→16000 + normalização funcionam.
- ⚠️ `pc.stt` devolveu `''` no `teste_python.wav`: a gravação estava
  quase muda (RMS 27/32768, pico 0.034). Não é o código — é ganho de
  captura do microfone no Pop!_OS. Normalizar 28x só amplifica ruído.
- ⏳ Falta: microfone com ganho decente, e o loop completo
  `server_voz` ↔ `audio_client` de ponta a ponta.

**Próximo passo planejado**
- Ajustar ganho de entrada do mic (Ajustes → Som, ou `alsamixer` F4) e
  validar com `python3 -m pc.push_to_talk` (mirar pico > ~0.1).
- Loop completo `server_voz` ↔ `audio_client` em localhost; depois pelo
  túnel SSH com a placa do robô.
- Sub-etapas seguintes: TTS da resposta na placa do robô (`espeak-ng` ou
  `piper`); trocar o operador humano por regras + IA.

---

## 2026-09-04

### Troca de hardware: Raspberry Pi → Jetson

**O que foi feito**
- A placa dentro da cabeça do robô deixou de ser um Raspberry Pi e passou a
  ser uma **Jetson**. `src/rasp/` → `src/jetson/` (`git mv`); todo import,
  docstring, comentário e doc que citava "Raspberry Pi"/"Rasp"/"Pi" nos
  arquivos ativos (`src/`, `docs/`, `README.md`) foi atualizado para
  "Jetson". Entradas antigas deste log (datadas) não foram reescritas —
  valiam para o hardware da época, ver nota no topo do arquivo.
- `experiments/audio_openai/` (spike parado, fora do caminho atual) só
  ganhou as correções de referência que quebrariam de fato (o caminho
  `src/rasp/audio.py` citado no README) — os comentários de desempenho
  específicos do Pi 3 antigo (ex.: custo de handshake TLS) foram deixados
  como estão, por serem uma observação histórica daquele hardware.

**Decisões importantes**
- **Ainda não há microfone nem alto-falante na Jetson.** Em vez de esperar
  o hardware de áudio chegar para ter algo testável, a etapa (d) foi
  dividida:
  - **(d1)** um "chat" de **texto puro** Jetson ↔ PC — sem nenhuma
    dependência nova, reaproveitando `jetson/client.py` (etapas a/b) e um
    novo `pc/server_chat.py` no lugar do eco `.upper()`.
  - **(d2)** o pipeline de áudio de verdade (`server_voz.py` +
    `audio_client.py`), que já existia e fica como está, aguardando o
    microfone.
- **Extraída a camada de resposta para `pc/cerebro.py`.** A função
  `responder(texto) -> texto` (hoje: imprime a mensagem e lê a resposta
  digitada por um operador humano no PC — o "outro usuário" da conversa)
  estava duplicada dentro de `server_voz.py`. Agora mora só em
  `pc/cerebro.py` e é chamada tanto por `server_chat.py` (texto) quanto por
  `server_voz.py` (texto já transcrito de áudio). Isso significa que:
  - trocar áudio por texto (ou vice-versa) é só trocar de servidor —
    nenhum dos dois sabe como a mensagem chegou;
  - trocar o operador humano pela IA de verdade é mexer só em
    `pc/cerebro.py`, sem tocar em nenhum dos dois servidores — ver
    [`docs/roadmap-ia-conversacional.md`](docs/roadmap-ia-conversacional.md).
- **O máximo do trabalho continua no PC.** `jetson/client.py` não mudou:
  ele já era o cliente mínimo (lê teclado, manda, mostra resposta) desde a
  etapa (a). O PC é quem ganhou o novo servidor e a camada de "cérebro".

**O que funcionou / não funcionou**
- ✅ `pc.server_chat` + `jetson.client` em localhost: mensagem digitada na
  Jetson chega ao PC, resposta digitada pelo operador volta e aparece no
  terminal da Jetson — mesmo comportamento de ida-e-volta já validado nas
  etapas (a)/(b), agora com um humano respondendo em vez de `.upper()`.
- ✅ `server_voz.py` importa `responder` de `pc.cerebro` em vez de definir a
  própria função — comportamento equivalente ao anterior, com um aviso
  extra no terminal do servidor quando a transcrição vier vazia (antes essa
  mensagem estava embutida na função de resposta; agora é um print à parte
  em `server_voz.py`, já que é específico de áudio).

**Próximo passo planejado**
- Rodar `pc.server_chat` ↔ `jetson.client` pelo túnel SSH com a Jetson de
  verdade (mesmo modelo das etapas a/b), não só em localhost.
- Quando o microfone/speaker chegar na Jetson: validar (d2) de ponta a
  ponta e então trocar `pc/cerebro.py` por regras + IA (fases 1-3 do
  roadmap de IA conversacional).

---

## 2026-09-08

### Etapa (d1) validada na Jetson

- ✅ `pc.server_chat` ↔ `jetson.client` rodou na Jetson de verdade por
  conexão local (não só em localhost no notebook): mensagem digitada na
  Jetson chega ao PC, o operador responde, a resposta aparece na Jetson.
  Fecha a etapa (d1).

### Etapa (d2) — áudio nos dois sentidos, testando em localhost no PC

**Decisão: a síntese de voz (TTS) roda no PC, não na Jetson.** O roadmap
original mandava a Jetson sintetizar. Mudou para manter a Jetson só como
"ouvido e boca": a voz do robô fica ao lado do "cérebro" (`pc/cerebro.py`),
trocável sem tocar no hardware da cabeça, e o protocolo já leva `AUDIO` nos
dois sentidos — devolver um WAV curto é simétrico ao áudio que a Jetson já
manda. Ver [`docs/roadmap-comunicacao.md`](docs/roadmap-comunicacao.md) (Fase 4).

**O que foi feito**
- `src/common/audio_io.py` (novo) — captura e reprodução compartilhadas
  entre `jetson/audio_client.py` e `pc/push_to_talk.py`. Contém o `Gravador`
  de duração variável (ENTER começa / ENTER para, sem VAD) e o `Cronometro`,
  antes só dentro de `push_to_talk.py`; mais `array_para_wav_bytes` e
  `tocar_wav_bytes` (ponte com o protocolo, que carrega WAV). Depende de
  sounddevice + numpy — fica em `common/` mas `protocol.py` continua sem
  dependência (quem só troca texto não importa `audio_io`).
- `src/pc/tts.py` (novo) — `sintetizar(texto) -> bytes` (WAV) via `espeak-ng`
  (voz `pt-br`, `LSA_TTS_VOZ` / `LSA_TTS_WPM` para ajustar). Isolado como o
  `stt.py`: trocar por `piper` depois é mexer só aqui. `espeak-ng` é pacote
  apt, sem modelo para baixar — evita repetir o problema do xet do whisper
  na rede da PUC.
- `src/pc/server_voz.py` — depois de `cerebro.responder()`, sintetiza a
  resposta e manda como `AUDIO` (`send_audio`). Se `espeak-ng` não estiver
  instalado, cai para `send_texto` (fallback) e avisa no start.
- `src/jetson/audio_client.py` — reescrito: captura via `audio_io.Gravador`
  (ENTER/ENTER, duração variável) no lugar dos 5 s fixos; a resposta agora
  é `AUDIO` e toca com `tocar_wav_bytes`; se vier `TEXTO` (fallback), só
  imprime. 3º argumento opcional = índice do microfone.
- `src/pc/push_to_talk.py` — passou a importar `Gravador`/`Cronometro` de
  `common.audio_io` (fim da duplicação); comportamento igual.
- `src/jetson/audio.py` — mantido como está (captura por duração fixa),
  ainda usado pelo seu autoteste e por `testar_microfone.py`.
- `requirements` do PC: nota que `espeak-ng` é apt, não pip.

**O que funcionou / não funcionou**
- ✅ Testes offline (sem mic, sem espeak-ng): `array_para_wav_bytes` gera
  WAV PCM 16-bit 16 kHz mono válido; round-trip pelo `protocol` (socketpair)
  devolve os bytes idênticos; `.texto` numa mensagem `AUDIO` levanta
  `ValueError`; `tts.sintetizar('')` → `ErroDeTTS`; `server_voz` sobe, avisa
  da falta do `espeak-ng`, ignora mensagem de tipo errado e trata desconexão.
- ⏳ Falta rodar o loop completo com microfone e alto-falante reais no PC
  (fala → transcrição → resposta digitada → voz tocada), e instalar
  `espeak-ng` (`sudo apt install -y espeak-ng`) para sair do fallback de texto.

**Próximo passo planejado**
- `sudo apt install -y espeak-ng` no PC; rodar `pc.server_voz` ↔
  `jetson.audio_client` em localhost e validar o ciclo de voz de ponta a ponta.
- Depois: mesmo loop pelo túnel SSH quando a Jetson tiver mic/speaker;
  então trocar `pc/cerebro.py` por regras + IA.

---

## 2026-09-14

### Inversão do pipeline de voz: mic no PC, resposta falada pela Jetson (Piper)

**Contexto:** a Jetson já tem mic e speaker reais funcionando (PrimeSense +
HDMI, ver bring-up de 2026-09-10) e já existia neste checkout um WIP não
commitado (`jetson/mic_vad.py` + `jetson/chat_client.py`) implementando o
caminho original: mic da Jetson → PC transcreve/decide/sintetiza
(espeak-ng) → Jetson só toca o áudio recebido. Decidido inverter essa
etapa: o mic passa a ser o do PC, e quem fala a resposta passa a ser a
Jetson (Piper, via `jetson/bin/say`), não o PC.

**Por que:** o mic da Jetson (PrimeSense) disputa a mesma interface USB
com a câmera — abrir os dois ao mesmo tempo trava a interface de áudio
(ver nota em `Camera_Simples.iniciar_sensor`, mesmo arquivo com mudanças
locais não commitadas). E o TTS local da Jetson (Piper) já soa bem melhor
que o espeak-ng do PC (`pc/tts.py`). "Por enquanto" — não é a arquitetura
final, é um jeito de testar sem o conflito de USB e com voz melhor.

**O que foi feito**
- `src/jetson/tts_server.py` (novo) — servidor TCP na Jetson (porta 5001
  por padrão, para conviver com a porta 5000 do `pc.server_voz`), recebe
  mensagem TEXTO e fala com `jetson/bin/say -1 "<texto>"` (Piper) no
  speaker HDMI. Python 3.6 (sem `from __future__ import annotations`,
  mesma regra de `mic_vad.py`/`chat_client.py`).
- `src/pc/voice_client.py` (novo) — cliente TCP no PC: grava o mic do PC
  (ENTER/ENTER, reaproveitando `common.audio_io.Gravador`, igual
  `push_to_talk.py`), transcreve com `pc.stt`, chama
  `pc.cerebro.responder()` (operador humano digita a resposta, sem
  mudança nessa parte) e manda o texto da resposta pra Jetson falar.
- `src/pc/push_to_talk.py` — `_preparar` virou `preparar_audio` (função
  pública) só para `voice_client.py` poder reaproveitar a normalização de
  mic fraco sem duplicar o código.

**Decisão importante:** o "cérebro" continua no PC (operador digita a
resposta ali, onde já está vendo a transcrição) — não foi portado pra
Jetson. A Jetson só recebe o texto pronto e fala; isso manteve a mudança
pequena (dois arquivos novos, nenhum protocolo novo — reaproveita TEXTO
de `common/protocol.py`).

**O que funcionou / não funcionou**
- ✅ Teste local na própria Jetson: `jetson.tts_server` em
  `127.0.0.1:5001` + um cliente de teste mandando TEXTO pelo protocolo —
  o servidor chamou `say -1`, falou e só devolveu o ack ("ok") depois da
  fala terminar, sem erro no log.
- ⏳ Não testado ainda de verdade PC↔Jetson pela rede (só localhost na
  Jetson). IP da Jetson na rede local hoje: `192.168.0.103` (WiFi) /
  `100.116.50.67` (Tailscale) — usar num dos dois como argumento de
  `pc.voice_client`.

**Pendência importante:** estes arquivos (e a mudança em
`push_to_talk.py`) existem só nesta cópia da Jetson — não foram
commitados nem empurrados. A convenção do projeto é a Jetson só *puxar*
git (commits são feitos no notebook) — então rodar isso a partir de um
notebook exige ou commitar+empurrar a partir daqui (fora da convenção
usual), ou copiar os arquivos novos pro notebook manualmente e commitar
de lá como de costume.

**Próximo passo planejado**
- Rodar `pc.voice_client <ip-da-jetson>` de um notebook de verdade contra
  `jetson.tts_server` rodando na Jetson, validar o ciclo completo.
- Decidir o destino do WIP `mic_vad.py`/`chat_client.py` (caminho
  original, mic na Jetson) — manter como alternativa ou descartar depois
  que a inversão for validada.
