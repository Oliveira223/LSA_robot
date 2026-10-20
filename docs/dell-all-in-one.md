# Robô no Dell all-in-one (Ubuntu 26.04, x86_64)

Tudo roda numa máquina só: câmera (webcam), YOLO de rosto (CPU), microfone +
Vosk (STT), cérebro (Ollama `llama3.2:1b`) e voz (Piper). Antes eram duas
Jetsons (robot2: câmera/STT; robot1: cérebro/voz). O protocolo é o mesmo —
o app conecta em `127.0.0.1:5005` em vez de `10.10.10.1:5005`.

## Instalação

1. **Uma vez, com root** (desktop X11, áudio, libGL): `sudo bash scripts/dell/instalar-sistema.sh` e reiniciar.
   Depois, trocar o OpenCV sem janela pelo completo:
   `uv pip uninstall --python experiments/camera_prime_sense/venv/bin/python opencv-python-headless && uv pip install --python experiments/camera_prime_sense/venv/bin/python "opencv-python<5"`
2. Sem root (já feito): `uv` + venv Python 3.12 em `experiments/camera_prime_sense/venv`
   (opencv, vosk, piper-tts, scipy), Ollama em `~/.local/ollama`, modelos em `~/.ollama`,
   `~/Bitnet_TTS` (cérebro), `~/piper` (voz; `~/piper/piper` é um shim do binário arm64),
   `~/dev/vosk-model-small-pt-0.3`.
3. Serviços de usuário (`scripts/dell/*.service` → `~/.config/systemd/user/`): `ollama` e
   `lsa-cerebro` (servidor_conversa na 5005). `scripts/dell/openbox-autostart` abre o app no login.

## Uso

```
camera-simples start      # abre a tela (webcam + YOLO + mic + chat)
camera status | type "oi" | say "texto" | log
```

## O que mudou no código (perfil PC = não-aarch64)

- `Camera_Simples.py` / `camera_utils.py`: `--camera auto|primesense|webcam`; sem PrimeSense usa a
  webcam (`WebcamCor`), sem profundidade (logo sem distância/saudação por proximidade).
- `jetson/mic_vad.py`: sem placa PrimeSense no `arecord -l`, usa o mic padrão (`LSA_MIC_DEVICE` muda).
- `jetson/conversa_client.py`: host padrão `127.0.0.1` (`LSA_CEREBRO_HOST`), `paplay` no sink padrão
  (`LSA_SINK`), sem keepalive HDMI.
- `bin/camera-simples`: perfil `pc` não espera/reseta USB nem mata o PulseAudio (`LSA_PERFIL` força).
- `~/Bitnet_TTS/memoria.py`: `DB_PATH` apontado pra `~/Bitnet_TTS/memoria.db`.

## Pendente / não testado
- Mic e alto-falante reais e a janela (precisam do passo 1); ajustar `--mic-limiar`/ganho do mic do Dell.
- Saudação por distância (precisaria estimar distância pelo tamanho do rosto).
