# PROGRESSO — Pipeline de comunicação Rasp ↔ PC

Log cronológico do projeto de comunicação (áudio → PC → texto → voz).
Cada entrada: data, o que foi tentado, o que funcionou / não funcionou,
decisões importantes, próximo passo.

Ordem de progressão planejada:
- **(a)** dois terminais no mesmo notebook (localhost), só texto, via socket
- **(b)** mesmo código, via túnel SSH entre notebook e Raspberry Pi
- **(c)** mesmo código, via WiFi direto (IP real na rede local)
- **(d)** integração de áudio (captura, envio, transcrição, TTS)

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
- **Transporte de código PC → Pi: `rsync` por enquanto** (só `common/` +
  `rasp/`), porque não exige configurar credencial do GitHub no Pi.
  Migrar para `git` depois, quando a autenticação do Pi estiver pronta.
- Comandos longos no Pi devem rodar dentro de `tmux` para sobreviver a
  quedas do SSH (aprendido na queda durante o `apt install`).

**Próximo passo planejado**
- Confirmar a etapa (a) rodando no PC (server + client locais).
- Etapa (b): `server.py` no PC; `rsync` de `common/` + `rasp/` para o Pi;
  túnel reverso `ssh -R 5000:localhost:5000 admin@192.168.0.102`;
  rodar `client.py 127.0.0.1 5000` no Pi por dentro do túnel.
  Sem mudança de código — só procedimento.
