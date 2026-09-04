# Roadmap — Pipeline de Áudio Jetson ↔ PC (Robô)

Objetivo final: Jetson (dentro da cabeça do robô) captura áudio, envia por WiFi para o PC, que processa e devolve texto; a Jetson converte esse texto em voz e toca no speaker.

```
[Mic na Jetson] → WiFi → [PC: STT] → texto → WiFi → [Jetson: TTS] → [Speaker]
```

A estratégia é validar cada camada isoladamente antes de integrar. Cada fase tem um critério de sucesso claro — só avance quando a fase anterior estiver 100% estável.

---

## Fase 0 — Preparação de rede

**O que fazer:**
- Conectar Jetson e PC na mesma rede WiFi.
- Configurar IP fixo (ou reserva DHCP no roteador) para a Jetson, para não perder o endereço a cada reboot.
- Testar conectividade básica.

```bash
# Na Jetson
hostname -I

# No PC
ping <IP_DO_RASP>
```

**Critério de sucesso:** ping estável, sem perda de pacotes, latência baixa (<10ms em rede local).

---

## Fase 1 — Comunicação de texto manual (validar o canal)

**O que fazer:**
- Criar um servidor socket TCP simples no PC.
- Criar um cliente socket na Jetson que manda texto digitado manualmente.
- PC processa (mesmo que seja só `.upper()` por enquanto) e devolve resposta.

**Por que pular o netcat puro:** você vai precisar de um protocolo próprio (indicar tamanho do payload, tipo de mensagem) — melhor já estruturar isso em Python desde o início.

**Critério de sucesso:** mensagem digitada na Jetson chega no PC, resposta processada volta pra Jetson, sem travar ou corromper dados.

---

## Fase 2 — Áudio local (sem rede ainda)

**O que fazer:**
- Na Jetson: testar captura de microfone (`arecord`, depois `sounddevice`/`pyaudio` em Python). Gravar em 16kHz mono (suficiente pra voz, mais leve pra transmitir).
- No PC: testar transcrição local com um arquivo `.wav` copiado manualmente (via `scp`), usando `faster-whisper`.

**Critério de sucesso:** grava na Jetson → copia manualmente pro PC → Whisper transcreve corretamente.

---

## Fase 3 — Áudio pela rede (sem processar ainda)

**O que fazer:**
- Unir Fase 1 + Fase 2: em vez de `scp` manual, enviar os bytes do `.wav` via socket.
- Importante: mandar o **tamanho do arquivo primeiro** (ex: 8 bytes) antes dos dados, porque TCP não garante que tudo chegue num único `recv()` — sem isso o arquivo pode chegar cortado.

**Critério de sucesso:** arquivo gravado na Jetson chega íntegro no PC (mesmo checksum/mesma duração) e toca normalmente no PC.

---

## Fase 4 — Pipeline completo (STT → texto → TTS)

**O que fazer:**
- PC recebe áudio, roda `faster-whisper`, devolve o texto transcrito pelo mesmo socket (ou nova conexão).
- Jetson recebe o texto e sintetiza voz localmente.

**Opções de TTS na Jetson (do mais leve ao mais pesado):**
- `espeak-ng` — robótico, muito leve, roda sem esforço no Pi. Combina até com estética de robô.
- `piper` — TTS neural, leve o suficiente pro Pi, com vozes bem mais naturais que espeak.

**Critério de sucesso:** fala captada pelo mic → texto correto no PC → voz sintetizada e tocada no speaker do robô, em um ciclo completo.

---

## Fase 5 — Streaming em tempo real (avançado, opcional)

**O que fazer (depois que o fluxo em lote estiver 100% estável):**
- Implementar detecção de silêncio/VAD (voice activity detection) na Jetson, para só enviar áudio quando alguém está falando, em vez de gravar blocos fixos de tempo.
- Considerar WebSockets em vez de TCP puro para facilitar streaming contínuo.
- Avaliar UDP para o áudio bruto se latência for mais crítica que garantia de entrega (com cuidado — pode perder pacotes).

**Critério de sucesso:** conversa fluida, sem precisar apertar botão pra "começar a gravar", com latência aceitável (idealmente < 1-2s por rodada).

---

## Resumo

| Fase | O que valida |
|---|---|
| 0 | Rede básica (ping, IP fixo) |
| 1 | Comunicação de texto manual via socket |
| 2 | Áudio funcionando localmente em cada ponta |
| 3 | Transferência de arquivo de áudio pela rede |
| 4 | Pipeline completo STT + TTS |
| 5 | Streaming em tempo real |

## Notas técnicas importantes

- **Sempre mande o tamanho do payload antes dos dados** em qualquer transferência via TCP.
- **16kHz mono** é o padrão recomendado para voz — reduz banda sem perder qualidade de reconhecimento.
- **IP fixo na Jetson** evita dor de cabeça recorrente com scripts que hardcodeiam endereço.
- Teste cada fase isoladamente antes de integrar — facilita MUITO o debug quando algo quebra.