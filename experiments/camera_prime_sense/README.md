# ◈ PrimeSense Carmine — Painel de Controle

Interface gráfica (GUI) em **CustomTkinter** para visualização em tempo real dos streams de uma câmera de profundidade **PrimeSense Carmine 1.09 (Short Range)** através do **OpenNI2**.

Permite alternar entre os sensores de profundidade, infravermelho e cor, exibir múltiplos streams simultaneamente em grade, aplicar colormaps na imagem de profundidade e realçar a qualidade visual dos frames.

---

## Funcionalidades

- **Modos single:** Profundidade (Depth), Infravermelho (IR) e Cor (RGB).
- **Modos multi:**
  - **Dual** — Depth + IR lado a lado.
  - **Trial** — Depth + IR + Color em grade (2 em cima, 1 centralizado embaixo).
- **Colormaps de profundidade:** JET, TURBO, PLASMA, INFERNO e HOT, selecionáveis em tempo real.
- **Realce de imagem** (toggle "Melhorar qualidade"): filtro bilateral, equalização adaptativa de contraste (CLAHE) e *unsharp mask*.
- **Tratamento do stream de profundidade:** normalização para 8 bits, *inpainting* (TELEA) para preencher buracos de leitura (pixels = 0) e suavização bilateral.
- **Labels** sobrepostos em cada frame nos modos multi e indicador de status do dispositivo na sidebar.
- Encerramento limpo dos streams e do OpenNI2 ao fechar a janela.

---

## Detecção de rosto, resolução e desempenho

- **YOLO de rosto** (`camera_utils.py`): roda num processo separado, com entrada 128 px (~2 detecções/s na Jetson, o dobro do antigo 160). As caixas são suavizadas entre detecções e somem após 1,5 s sem confirmação. Há um checkbox "Detectar rostos (YOLO)" no painel; `--no-faces` desliga e `--face-size N` muda a entrada (múltiplo de 32).
- **Modelo mais leve:** se existirem `cfg/yolov3-tiny-face.cfg` e `model-weights/yolov3-tiny-face.weights`, eles são usados no lugar do yolov3-face completo.
- **Resolução:** o sensor é consultado em runtime e a cor usa 640×480@30 por padrão (medido: 29 fps reais). 1280×1024 é anunciado a 30 fps mas entrega ~5 fps (limite do USB 2.0) e trava se combinado com profundidade; só com `--res 1280x1024`.
- **Leveza:** o realce (CLAHE + unsharp) roda no tamanho de exibição, sem filtro bilateral; a profundidade não usa mais `inpaint` (fechamento morfológico no lugar); redimensionamento com INTER_LINEAR.

## Terminal de controle (`camera`)

**Instalação (uma vez por Jetson):** os atalhos não são versionados, então crie os links em `~/.local/bin` (já está no `PATH` depois do login):

```
ln -sf ~/dev/LSA_robot/src/jetson/bin/camera        ~/.local/bin/camera
ln -sf ~/dev/LSA_robot/src/jetson/bin/camera-simples ~/.local/bin/camera-simples
```

O `camera-simples` (launcher) abre/encerra o app e espera a PrimeSense aparecer no USB; o `camera` é o terminal de controle. Para a voz do robô, atualize também o `servidor_conversa.py` na Jetson 1 (veja *Voz do robô* abaixo).

`camera` (`src/jetson/bin/camera`, atalho em `~/.local/bin/camera`) controla o app em execução por um socket Unix (`/tmp/camera.sock`), sem reiniciar nada. Sem argumentos abre um terminal interativo; com argumento roda um comando e sai (`camera greet on`). Os comandos são em inglês (os nomes antigos em português continuam funcionando, fora do `help`).

| Comando | O que faz |
|---|---|
| `status` | estado de câmera, profundidade, rostos, microfone (com diagnóstico), cérebro, voz e saudação |
| `greet [on\|off\|text <frase>\|dist <min> <max>\|interval <s>]` | liga/desliga e ajusta o "bom dia" por proximidade, ao vivo |
| `say [texto]` | o robô fala o texto com a **voz dele** (Piper, a mesma das respostas). Sem texto abre o prompt `say> `, que fala cada linha digitada |
| `type [pergunta]` | digita para o robô como se tivesse sido falado (vai ao cérebro). Com a pergunta, envia e mostra a resposta. Sem argumento abre o prompt `type> `, onde tudo que você digita vai ao cérebro e as respostas aparecem |
| `shush` | interrompe a fala do robô agora (corta o áudio e descarta o resto da resposta) |
| `chat [on\|off]` | liga/desliga a conversa por voz: desligada, o microfone só transcreve e o robô não responde ao ambiente (`type` e `say` continuam funcionando). `--chat-off` abre já desligada |
| `listen` | mostra ao vivo o que o microfone ouve e as respostas do robô (Ctrl+C sai) |
| `restart mic` | recria o ouvinte do microfone dentro do app e confere se chegou áudio; se o áudio USB da PrimeSense travou, avisa que só um replug resolve |
| `start [flags]`, `restart [flags]`, `stop` | abrem/reiniciam/encerram o app pelo `camera-simples` (SIGTERM primeiro, sem reset USB) |
| `log [all\|important\|today [N]\|days\|day <data> [N]]` | logs com data, hora e cor, ao vivo ou por dia (veja *Logs* abaixo) |

A saudação começa desligada; `--saudar` a abre ligada. Módulos: `controle.py` (servidor do socket), `comandos.py` (os comandos), `saudacao.py`. Sem microfone (`--no-mic` ou mic indisponível na abertura) a janela de texto e a conversa continuam funcionando por digitação.

**Voz do robô (`say` e saudação):** o Piper só existe na Jetson 1, então ela sintetiza o texto e esta Jetson toca. Isso exige o `src/jetson1/servidor_conversa.py` novo na Jetson 1 (copie e reinicie o servidor). Com o servidor antigo, o `status` mostra "voz: local" e o `say` cai para o `spd-say` (voz robótica, pouco confiável dentro do app), avisando o motivo.

## Logs

O app imprime cada evento com data, hora, **nível** e **tag**, sem que cada `print` do projeto precise mudar (`camera_log.py` envolve `stdout`/`stderr`):

```
2026-09-30 15:36:46 INFO  [saudacao] falando 'teste do log' (voz do robo (Piper, Jetson 1))
2026-09-30 15:36:48 WARN  [mic_vad] desisti de religar o arecord — mic offline
```

Níveis: `DEBUG` (ruído: quadros de profundidade, falas descartadas, áudio recebido), `INFO`, `WARN` e `ERROR`. Dois destinos:

- **Sessão** (`/tmp/camera-simples.log`, recriado a cada abertura): tudo, com o ruído como `DEBUG`.
- **Arquivo por dia** (`logs/camera/AAAA-MM-DD.log`, fora do git, guardado por 30 dias): só o que importa, isto é, avisos e erros (inclusive tracebacks), conexões com a Jetson 1, frases reconhecidas, respostas do robô, saudações, comandos do terminal `camera` e início/fim do app. Linhas iguais e seguidas viram "(a linha acima se repetiu mais N vezes)".

`camera log` mostra tudo isso com cores (vermelho = erro, amarelo = aviso, cinza = ruído, uma cor por tag nos eventos). Sem argumento acompanha a sessão sem o ruído; `log all` inclui o ruído; `log important` acompanha o arquivo de hoje; `log today [N]`, `log days` e `log day AAAA-MM-DD [N]` leem os arquivos diários. A pasta pode ser trocada com `CAMERA_LOG_DIR`.

## Requisitos

### Hardware
- Câmera **PrimeSense Carmine 1.09 / Xtion** (ou compatível com OpenNI2).
- Porta USB 2.0 ou superior.

### Software
- **Linux** x86_64 ou aarch64 (o diretório do OpenNI2 é detectado automaticamente; use `OPENNI2_REDIST` para forçar).
- **Python 3.8+**.
- Runtime do **OpenNI2** instalado no sistema (bibliotecas `.so`), além do pacote Python `primesense`.

---

## Instalação

### 1. Runtime do OpenNI2 (nível de sistema)

O pacote Python `primesense` é apenas um *wrapper* — ele precisa das bibliotecas nativas do OpenNI2 instaladas.

**Debian / Ubuntu:**
```bash
sudo apt update
sudo apt install libopenni2-0 libopenni2-dev
```

O código agora **detecta o diretório do OpenNI2 automaticamente**
(`/usr/lib`, `/usr/lib/aarch64-linux-gnu`, `/usr/lib/x86_64-linux-gnu`, ...).
Se necessário, force com a variável de ambiente `OPENNI2_REDIST`:
```bash
find / -name "libOpenNI2.so*" 2>/dev/null
export OPENNI2_REDIST=/caminho/para/o/diretorio
```

### 2. Dependências Python

#### Jetson / aarch64 (JetPack, Python 3.6) — usar `requirements.txt`

O JetPack traz OpenCV e NumPy compilados só para o Python 3.6 do sistema;
o `venv` precisa enxergá-los. E o `pip` antigo do JetPack não lê wheels
`manylinux2014`, então atualize-o primeiro.

```bash
python3 -m venv --system-site-packages venv
source venv/bin/activate
pip install -U "pip>=21.3,<22" "setuptools<60" wheel
pip install -r requirements.txt
```

> `requirements.txt` fixa `customtkinter==3.11` (último que roda em
> Python 3.6). `Run_Camera_Prime_Sense.py` traz um *shim* de
> compatibilidade no topo do arquivo para a API antiga do customtkinter
> (`CTkFont`, `CTkOptionMenu`, `font=` → `text_font=`).

#### Desktop x86_64 (Python 3.8+) — usar `requirements-desktop.txt`

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements-desktop.txt
```

---

## Como usar

```bash
python3 Run_Camera_Prime_Sense.py
```

> **Importante:** feche qualquer `NiViewer` / outro programa OpenNI2 antes
> de rodar. Se aparecer `Failed to set USB interface!`, o sensor está em
> uso por outro processo:
> ```bash
> pkill -f NiViewer; pkill -f XnSensorServer
> ```

Ao iniciar, a aplicação abre no modo **Profundidade**. Use a sidebar para alternar entre os modos e ajustar as opções.

---

## Controles

| Botão / Controle        | Ação                                                |
|-------------------------|-----------------------------------------------------|
| **Profundidade**        | Stream de depth com colormap.                       |
| **Infravermelho**       | Stream IR em escala de cinza (convertida p/ BGR).   |
| **Cor (RGB)**           | Stream de cor.                                      |
| **Dual (Depth + IR)**   | Depth e IR simultâneos lado a lado.                 |
| **Trial (todos)**       | Depth, IR e Color simultâneos em grade.             |
| **Colormap depth**      | Troca o mapa de cores aplicado à profundidade.      |
| **Melhorar qualidade**  | Liga/desliga o realce de imagem.                    |
| **Sair**                | Encerra streams, descarrega o OpenNI2 e fecha.      |

---

## Estrutura do código

A aplicação é composta por uma única classe `XtionAppGUI` (herda de `ctk.CTk`), organizada em blocos:

- **Build UI** — `_build_sidebar`, `_build_video_area`: construção da interface.
- **Sensor** — `inicializar_sensor`: inicializa o OpenNI2 e cria os streams.
- **Controles** — `mudar_modo`, `atualizar_botoes`, `_parar_todos`, `_on_cmap_change`.
- **Processamento** — `ler_depth`, `ler_ir`, `ler_color`, `melhorar`, `adicionar_label`, `montar_grid`.
- **Loop de vídeo** — `atualizar_video`: agendado via `after(16, ...)` (~60 FPS alvo).
- **Cleanup** — `ao_fechar`: encerramento seguro.

---

## Solução de problemas

- **`Falha ao iniciar sensor` / erro no `initialize`:** confirme que o runtime do OpenNI2 está instalado e que o caminho passado para `openni2.initialize(...)` está correto.
- **Câmera não detectada:** verifique a conexão USB e as permissões do dispositivo (pode ser necessário configurar regras *udev* ou rodar com privilégios adequados).
- **`ImportError: primesense`:** instale as dependências com `pip install -r requirements.txt` dentro do ambiente virtual ativo.
- **Conflito entre streams:** o modo Dual evita o stream de cor de propósito, pois alguns dispositivos não suportam Depth + IR + Color simultaneamente. Se o modo Trial falhar, prefira Dual.

---

## Observações

- Testado em Jetson (aarch64, JetPack/Ubuntu 18.04, Python 3.6) e desktop Linux x86_64; portabilidade para Windows/macOS exige ajustar a inicialização do OpenNI2.
- O loop de atualização usa `after(16, ...)`; em hardware mais limitado, aumentar esse intervalo reduz o uso de CPU.

