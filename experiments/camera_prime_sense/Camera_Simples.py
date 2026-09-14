#!/usr/bin/env python3
"""
Visualização simples da câmera PrimeSense: imagem RGB em tela cheia, com um
gráfico animado do nível do microfone (estilo onda de assistente de voz) em
miniatura num canto, e um bounding box verde ao redor de rostos detectados
(YOLOv3-face) na imagem RGB. Sem sidebar, sem outros modos — só isso.

Uso:
    python3 Camera_Simples.py [--corner tl|tr|bl|br] [--windowed]
    python3 Camera_Simples.py --no-faces          desliga a deteccao de rosto
    python3 Camera_Simples.py --face-size 128      entrada da rede menor = mais rapido, menos preciso

Sai com 'q' ou ESC.

Deteccao de rosto: YOLOv3 (Darknet, treinado no WIDER FACE por
github.com/sthanhng/yoloface) via cv2.dnn. Essa Jetson roda o OpenCV do
JetPack sem CUDA no modulo dnn, entao o YOLOv3 completo e pesado demais pra
rodar a cada frame (~5s/frame em 416x416 na CPU). Por isso a deteccao roda
num PROCESSO separado (nao thread — na pratica o cv2.dnn desse OpenCV velho
trava o main loop e ate o encerramento por sinal quando dividido so por
thread; processo isola isso de vez), em resolucao reduzida (--face-size,
padrao 160 -> ~1 deteccao/seg), e o laço principal so desenha o ultimo
bounding box encontrado sobre o frame atual — o video continua fluido, so a
caixinha "atualiza" com um pequeno atraso.
"""
import argparse
import glob
import multiprocessing as mp
import os
import signal
import subprocess
import sys
import time

import cv2
import numpy as np
from primesense import openni2

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# jetson.mic_vad (e, mais adiante, jetson.chat_client) vivem no repo
# LSA_robot, fora dessa pasta — ver README do Desktop sobre a divisao
# repo-vs-app-rodando. Import defensivo: sem o mic, a camera continua
# funcionando normalmente (mesmo espirito do detector de rosto ausente).
sys.path.insert(0, os.path.expanduser("~/dev/LSA_robot/src"))
try:
    from jetson.mic_vad import OuvinteVAD, ErroDeMic
except Exception as _erro_import_mic:
    OuvinteVAD = None
    ErroDeMic = Exception
    print(f"AVISO: jetson.mic_vad indisponivel ({_erro_import_mic}); "
          "seguindo sem indicador de microfone.", file=sys.stderr)
try:
    from jetson.chat_client import ClienteChat, ErroDeChat
except Exception as _erro_import_chat:
    ClienteChat = None
    ErroDeChat = Exception
    print(f"AVISO: jetson.chat_client indisponivel ({_erro_import_chat}); "
          "seguindo sem chat.", file=sys.stderr)
FACE_CFG = os.path.join(BASE_DIR, "cfg", "yolov3-face.cfg")
FACE_WEIGHTS = os.path.join(BASE_DIR, "model-weights", "yolov3-wider_16000.weights")


# ─── Deteccao de rosto (YOLOv3-face) num processo separado ──────────────
def _processo_detector(cfg, weights, tamanho, confianca, nms, fila_frame, fila_boxes):
    """Corpo do processo filho: carrega a rede uma vez e fica num loop
    bloqueante pegando o frame mais recente da fila e devolvendo as caixas.
    Roda isolado (multiprocessing, nao thread) pra nao competir com o laço
    de video/GUI do processo principal nem atrapalhar o encerramento dele."""
    net = cv2.dnn.readNetFromDarknet(cfg, weights)
    net.setPreferableBackend(cv2.dnn.DNN_BACKEND_OPENCV)
    net.setPreferableTarget(cv2.dnn.DNN_TARGET_CPU)
    nomes = net.getLayerNames()
    saidas_net = [nomes[int(i) - 1] for i in net.getUnconnectedOutLayers().flatten()]

    while True:
        frame = fila_frame.get()  # bloqueia ate ter um frame novo
        if frame is None:  # sentinela de parada
            break

        altura, largura = frame.shape[:2]
        blob = cv2.dnn.blobFromImage(frame, 1 / 255.0, (tamanho, tamanho), swapRB=True, crop=False)
        net.setInput(blob)
        saidas = net.forward(saidas_net)

        caixas, confs = [], []
        for saida in saidas:
            for det in saida:
                conf = float(det[5])
                if conf < confianca:
                    continue
                cx, cy, bw, bh = det[0] * largura, det[1] * altura, det[2] * largura, det[3] * altura
                x, y = int(cx - bw / 2), int(cy - bh / 2)
                caixas.append([x, y, int(bw), int(bh)])
                confs.append(conf)

        indices = cv2.dnn.NMSBoxes(caixas, confs, confianca, nms)
        resultado = [tuple(caixas[i]) for i in np.array(indices).flatten()] if len(indices) else []

        # so importa o resultado mais recente: esvazia antes de colocar o novo
        try:
            while True:
                fila_boxes.get_nowait()
        except Exception:
            pass
        fila_boxes.put(resultado)


class DetectorRosto:
    """Cliente do processo de deteccao: submit() manda o frame atual (numa
    versao reduzida, pra nao pagar o custo de serializar um frame gigante
    entre processos), boxes() devolve a ultima deteccao disponivel, ja
    reescalada pro tamanho do frame original."""

    def __init__(self, cfg, weights, tamanho=160, confianca=0.5, nms=0.4, ipc_max_lado=480):
        ctx = mp.get_context("spawn")  # spawn, nao fork: nao herda o handle
        # aberto do sensor OpenNI2 nem a conexao X11 do processo pai
        self.ipc_max_lado = ipc_max_lado
        self._escala = 1.0
        self._boxes = []
        self._fila_frame = ctx.Queue(maxsize=1)
        self._fila_boxes = ctx.Queue(maxsize=1)
        self._proc = ctx.Process(
            target=_processo_detector,
            args=(cfg, weights, tamanho, confianca, nms, self._fila_frame, self._fila_boxes),
            daemon=True,
        )
        self._proc.start()

    def submit(self, frame_bgr):
        altura, largura = frame_bgr.shape[:2]
        maior_lado = max(altura, largura)
        if maior_lado > self.ipc_max_lado:
            self._escala = self.ipc_max_lado / maior_lado
            envio = cv2.resize(frame_bgr, (int(largura * self._escala), int(altura * self._escala)))
        else:
            self._escala = 1.0
            envio = frame_bgr
        try:
            self._fila_frame.get_nowait()  # descarta frame antigo nao processado
        except Exception:
            pass
        try:
            self._fila_frame.put_nowait(envio)
        except Exception:
            pass

    def boxes(self):
        try:
            while True:
                self._boxes = self._fila_boxes.get_nowait()
        except Exception:
            pass
        if self._boxes and self._escala != 1.0:
            inv = 1.0 / self._escala
            return [(int(x * inv), int(y * inv), int(w * inv), int(h * inv)) for (x, y, w, h) in self._boxes]
        return self._boxes

    def parar(self):
        try:
            self._fila_frame.put_nowait(None)
        except Exception:
            pass
        self._proc.join(timeout=3)
        if self._proc.is_alive():
            self._proc.terminate()


def desenhar_rostos(cor, caixas):
    for (x, y, w, h) in caixas:
        cv2.rectangle(cor, (x, y), (x + w, y + h), (0, 255, 0), 2)
        cv2.putText(cor, "rosto", (x, max(0, y - 8)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)


# ─── OpenNI2: localizar o runtime ──────────────────────────────────────
def localizar_openni2_redist():
    candidatos = [
        "/usr/lib/aarch64-linux-gnu",
        "/usr/lib/x86_64-linux-gnu",
        "/usr/lib",
        "/usr/local/lib",
        os.environ.get("OPENNI2_REDIST", ""),
    ]
    for d in candidatos:
        if d and glob.glob(os.path.join(d, "libOpenNI2.so*")):
            return d
    for base in ("/usr/lib", "/usr/local/lib", "/opt"):
        hits = glob.glob(os.path.join(base, "**", "libOpenNI2.so*"), recursive=True)
        if hits:
            return os.path.dirname(hits[0])
    return ""  # deixa o OpenNI2 tentar o padrão do sistema


# ─── Sensor ─────────────────────────────────────────────────────────────
def iniciar_sensor():
    """So abre o stream de cor. O stream de profundidade nao e mais usado
    (a miniatura do canto agora e o grafico de som, nao a profundidade) —
    fora de nao servir mais pra nada aqui, era a parte mais pesada de CPU
    (inpaint, Canny, normalizacao por percentil a cada frame) e mais
    instavel do sensor nessa Jetson (trava/precisa de replug com
    frequencia)."""
    openni2.initialize(localizar_openni2_redist())
    dev = openni2.Device.open_any()
    color_stream = dev.create_color_stream()
    color_stream.start()
    return dev, color_stream


def parar_sensor(color_stream):
    try:
        color_stream.stop()
    except Exception:
        pass
    try:
        openni2.unload()
    except Exception:
        pass


# ─── Leitura de frames ──────────────────────────────────────────────────
def ler_color(color_stream):
    frame = color_stream.read_frame()
    img = np.frombuffer(frame.get_buffer_as_uint8(), dtype=np.uint8)
    img = img.reshape(frame.height, frame.width, 3)
    return cv2.cvtColor(img, cv2.COLOR_RGB2BGR)


# ─── Composição da tela ─────────────────────────────────────────────────
def _texto_com_contorno(img, texto, pos, escala, cor):
    """cv2.putText com um contorno preto por baixo, pra ficar legivel mesmo
    quando a onda de som (ou qualquer outra coisa clara) passa atras."""
    cv2.putText(img, texto, pos, cv2.FONT_HERSHEY_SIMPLEX, escala,
                (0, 0, 0), 3, cv2.LINE_AA)
    cv2.putText(img, texto, pos, cv2.FONT_HERSHEY_SIMPLEX, escala,
                cor, 1, cv2.LINE_AA)


class VisualizadorSom:
    """Forma de onda "de verdade" do microfone — picos de amplitude reais,
    nao uma senoide sintetica — com fundo TRANSPARENTE: desenha direto em
    cima do recorte da imagem da camera (so escurecido um pouco, pra dar
    contraste), entao o video continua aparecendo atras da onda. Estilo
    HUD/terminal de filme de hacker, nao um mini-monitor a parte.

    Le jetson.mic_vad.OuvinteVAD.amostras_recentes() (audio cru, mono,
    int16) e desenha o min/max de cada faixa de amostras como uma barra
    vertical — a mesma tecnica que editores de audio usam pra plotar forma
    de onda, entao os picos vem do audio de verdade."""

    COR_HUD = (255, 220, 0)      # ciano, mesma paleta dos outros paineis
    COR_FALA = (0, 255, 80)      # verde quando acima do limiar (falando)
    COR_OFFLINE = (0, 0, 255)    # vermelho — parou de verdade, nao e so silencio
    TINT = 0.15                  # quanto escurece o video por baixo (0 = 100% transparente)

    def desenhar(self, regiao, ouvinte):
        """Desenha em cima de `regiao` (recorte do frame da camera, ja do
        tamanho do painel) e devolve o resultado — nao cria fundo proprio."""
        preto = np.zeros_like(regiao)
        regiao = cv2.addWeighted(regiao, 1.0 - self.TINT, preto, self.TINT, 0)
        altura, largura = regiao.shape[:2]
        meio_y = altura // 2

        if ouvinte is None:
            return regiao

        nivel, limiar, gravando, vivo = ouvinte.estado()
        if not vivo:
            cv2.line(regiao, (0, meio_y), (largura, meio_y), self.COR_OFFLINE, 1, cv2.LINE_AA)
            _texto_com_contorno(regiao, "MIC OFFLINE", (largura // 2 - 78, meio_y - 10),
                                 0.5, self.COR_OFFLINE)
            return regiao

        cor_onda = self.COR_FALA if gravando else self.COR_HUD
        amostras = ouvinte.amostras_recentes()
        if len(amostras) < 2:
            cv2.line(regiao, (0, meio_y), (largura, meio_y), cor_onda, 1, cv2.LINE_AA)
            return regiao

        dados = np.frombuffer(amostras, dtype=np.int16).astype(np.float32)
        bucket = max(1, len(dados) // largura)
        usavel = (len(dados) // bucket) * bucket
        dados = dados[-usavel:].reshape(-1, bucket)
        minimos = dados.min(axis=1)
        maximos = dados.max(axis=1)

        # teto de amplitude derivado do limiar de fala (RMS) — voz normal
        # ocupa boa parte da altura do painel sem estourar toda hora
        teto = max(limiar * 6, 3000)
        n_colunas = len(minimos)
        for i in range(n_colunas):
            x = int(i * largura / n_colunas)
            y_topo = meio_y - int(np.clip(maximos[i] / teto, -1, 1) * (altura * 0.48))
            y_base = meio_y - int(np.clip(minimos[i] / teto, -1, 1) * (altura * 0.48))
            if y_topo == y_base:  # garante pelo menos 1px visivel mesmo em silencio total
                y_base += 1
            cv2.line(regiao, (x, y_topo), (x, y_base), cor_onda, 1, cv2.LINE_AA)

        estado_txt = "OUVINDO..." if gravando else "MIC"
        _texto_com_contorno(regiao, estado_txt, (8, 18), 0.45, cor_onda)

        return regiao


def compor_hud_transparente(fundo, visualizador, ouvinte, canto="br", margem=20,
                             escala=0.28, proporcao=0.6, rotulo="AUDIO WAVE"):
    """Desenha o painel de `visualizador` direto em cima do video (recorte
    de `fundo`) em vez de colar uma miniatura opaca — o video continua
    aparecendo atras da onda (so um pouco escurecido, ver
    VisualizadorSom.TINT), com a mesma moldura HUD (cantos tipo mira +
    rotulo) dos outros paineis."""
    cor_hud = (255, 220, 0)  # ciano tipo HUD, em BGR

    h, w = fundo.shape[:2]
    mw = int(w * escala)
    mh = int(mw * proporcao)

    if canto == "tl":
        x, y = margem, margem
    elif canto == "tr":
        x, y = w - mw - margem, margem
    elif canto == "bl":
        x, y = margem, h - mh - margem
    else:  # "br"
        x, y = w - mw - margem, h - mh - margem

    saida = fundo
    regiao = saida[y:y + mh, x:x + mw]
    saida[y:y + mh, x:x + mw] = visualizador.desenhar(regiao, ouvinte)

    tick = 14
    for (cx, cy, dx, dy) in [(x, y, 1, 1), (x + mw, y, -1, 1),
                              (x, y + mh, 1, -1), (x + mw, y + mh, -1, -1)]:
        cv2.line(saida, (cx, cy), (cx + dx * tick, cy), cor_hud, 2, cv2.LINE_AA)
        cv2.line(saida, (cx, cy), (cx, cy + dy * tick), cor_hud, 2, cv2.LINE_AA)

    cv2.putText(saida, rotulo, (x, y - 8), cv2.FONT_HERSHEY_SIMPLEX, 0.5,
                cor_hud, 1, cv2.LINE_AA)

    return saida


def _quebrar_linhas(texto, largura_max_px, fonte, escala, espessura):
    """Quebra `texto` em linhas que cabem em `largura_max_px` (usa
    cv2.getTextSize pra medir de verdade em vez de contar caracteres)."""
    palavras = texto.split()
    linhas = []
    atual = ""
    for palavra in palavras:
        candidato = (atual + " " + palavra).strip()
        (lw, _), _ = cv2.getTextSize(candidato, fonte, escala, espessura)
        if lw <= largura_max_px or not atual:
            atual = candidato
        else:
            linhas.append(atual)
            atual = palavra
    if atual:
        linhas.append(atual)
    return linhas or [""]


def desenhar_chat(fundo, mensagens, margem=20, altura_frac=0.5, largura_frac=0.32):
    """Painel de chat estilo HUD no canto superior direito, ate ~metade da
    altura da tela. Mensagens do usuario (transcritas do mic) alinhadas a
    ESQUERDA do painel, respostas do robo a DIREITA. `mensagens` e uma
    lista de (autor, texto) com autor "usuario"/"robo", mais recente por
    ultimo (mesmo formato de jetson.chat_client.ClienteChat.mensagens())."""
    if not mensagens:
        return fundo

    cor_hud = (255, 220, 0)      # ciano, mesma paleta dos outros paineis
    cor_robo = (120, 255, 120)   # verde suave, so pra diferenciar do usuario
    fonte = cv2.FONT_HERSHEY_SIMPLEX
    escala = 0.5
    espessura = 1
    altura_linha = 22
    pad = 12

    h, w = fundo.shape[:2]
    py = margem
    pw = int(w * largura_frac)
    ph = int(h * altura_frac)
    x0 = w - pw - margem
    x1 = w - margem

    saida = fundo
    regiao = saida[py:py + ph, x0:x1].copy()
    cv2.rectangle(regiao, (0, 0), (regiao.shape[1], regiao.shape[0]), (0, 0, 0), -1)
    saida[py:py + ph, x0:x1] = cv2.addWeighted(regiao, 0.45, saida[py:py + ph, x0:x1], 0.55, 0)

    cv2.rectangle(saida, (x0, py), (x1, py + ph), cor_hud, 1, cv2.LINE_AA)
    tick = 14
    for (cx, cy, dx, dy) in [(x0, py, 1, 1), (x1, py, -1, 1),
                              (x0, py + ph, 1, -1), (x1, py + ph, -1, -1)]:
        cv2.line(saida, (cx, cy), (cx + dx * tick, cy), cor_hud, 2, cv2.LINE_AA)
        cv2.line(saida, (cx, cy), (cx, cy + dy * tick), cor_hud, 2, cv2.LINE_AA)
    cv2.putText(saida, "CHAT", (x0, py - 8), fonte, 0.5, cor_hud, 1, cv2.LINE_AA)

    # monta os blocos de baixo pra cima (mais recente primeiro) ate estourar
    # a altura do painel, depois desenha em ordem cronologica normal
    largura_max_px = pw - 2 * pad
    blocos = []
    altura_usada = 0
    for autor, texto in reversed(mensagens):
        linhas = _quebrar_linhas(texto or "(vazio)", largura_max_px, fonte, escala, espessura)[:4]
        altura_bloco = len(linhas) * altura_linha + 8
        if altura_usada + altura_bloco > ph - 2 * pad:
            break
        blocos.append((autor, linhas))
        altura_usada += altura_bloco
    blocos.reverse()

    y = py + ph - pad - altura_usada
    for autor, linhas in blocos:
        cor = cor_hud if autor == "usuario" else cor_robo
        for linha in linhas:
            (lw, _), _ = cv2.getTextSize(linha, fonte, escala, espessura)
            tx = x0 + pad if autor == "usuario" else x1 - pad - lw
            cv2.putText(saida, linha, (tx, y + altura_linha - 6), fonte, escala,
                        cor, espessura, cv2.LINE_AA)
            y += altura_linha
        y += 8

    return saida


# ─── Janela em tela cheia ───────────────────────────────────────────────
def tamanho_tela_cheia():
    """Resolução do monitor via xrandr; usa 1920x1080 se não conseguir."""
    try:
        saida = subprocess.check_output(
            ["xrandr", "--current"], env=os.environ, timeout=3
        ).decode()
        for linha in saida.splitlines():
            if "*" in linha:
                modo = linha.split()[0]
                w, h = modo.split("x")
                return int(w), int(h)
    except Exception:
        pass
    return 1920, 1080


def _on_sigterm(signum, frame):
    # sem isso, um "kill" (SIGTERM) mata o processo na hora e pula o
    # 'finally' do main() — o OpenNI2/sensor da PrimeSense fica travado
    # (visto na pratica: precisou de um reset USB pra voltar a funcionar).
    # SystemExit propaga por 'finally' como qualquer excecao normal.
    raise SystemExit(0)


def main():
    signal.signal(signal.SIGTERM, _on_sigterm)

    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--corner", choices=["tl", "tr", "bl", "br"], default="br",
                     help="canto onde o grafico de som aparece (padrão: br)")
    ap.add_argument("--windowed", action="store_true",
                     help="abre em janela normal em vez de tela cheia")
    ap.add_argument("--no-faces", action="store_true",
                     help="desliga a deteccao de rosto (YOLOv3-face; roda em processo a parte, "
                          "~1 deteccao/seg, mas ainda consome uma CPU inteira)")
    ap.add_argument("--face-size", type=int, default=160,
                     help="entrada da rede YOLO em pixels (padrao 160; menor = mais rapido)")
    ap.add_argument("--face-conf", type=float, default=0.5,
                     help="confianca minima da deteccao de rosto (padrao 0.5)")
    ap.add_argument("--no-mic", action="store_true",
                     help="desliga o grafico de nivel do microfone")
    ap.add_argument("--mic-limiar", type=int, default=None,
                     help="limiar de RMS pra considerar 'falando' (ajuste fino do VAD)")
    ap.add_argument("--no-chat", action="store_true",
                     help="desliga o chat por voz com o PC (so fica o indicador de mic)")
    ap.add_argument("--chat-host", default="127.0.0.1",
                     help="IP do PC rodando pc.server_voz (padrao 127.0.0.1)")
    ap.add_argument("--chat-port", type=int, default=5000,
                     help="porta do pc.server_voz (padrao 5000)")
    args = ap.parse_args()

    detector = None
    if not args.no_faces:
        if os.path.isfile(FACE_CFG) and os.path.isfile(FACE_WEIGHTS):
            detector = DetectorRosto(FACE_CFG, FACE_WEIGHTS,
                                      tamanho=args.face_size, confianca=args.face_conf)
        else:
            print(f"AVISO: modelo YOLO de rosto nao encontrado ({FACE_CFG} / "
                  f"{FACE_WEIGHTS}); seguindo sem deteccao de rosto.", file=sys.stderr)

    # OpenNI2 abre a interface de video da PrimeSense PRIMEIRO, sem nada mais
    # mexendo no mesmo dispositivo USB ao mesmo tempo — visto na pratica
    # 2026-09-14 que abrir o mic (arecord -l + Popen) concorrente com o
    # Device.open_any() do OpenNI2 deixa a interface de audio travada
    # (arecord -l trava mesmo, precisa de replug fisico pra voltar). So
    # inicializa o mic/chat DEPOIS do sensor de video estar de pe.
    _dev, color_stream = iniciar_sensor()
    visual_som = VisualizadorSom()

    ouvinte = None
    if not args.no_mic and OuvinteVAD is not None:
        try:
            kwargs = {"limiar_fala": args.mic_limiar} if args.mic_limiar else {}
            ouvinte = OuvinteVAD(**kwargs)
        except ErroDeMic as e:
            print(f"AVISO: microfone indisponivel ({e}); seguindo sem indicador de mic.",
                  file=sys.stderr)

    cliente_chat = None
    if not args.no_chat and ClienteChat is not None:
        if ouvinte is None:
            print("AVISO: sem microfone, seguindo sem chat.", file=sys.stderr)
        else:
            try:
                # reaproveita o mesmo OuvinteVAD do indicador de mic — so pode
                # ter um arecord por vez no hw:2,0.
                cliente_chat = ClienteChat(args.chat_host, args.chat_port, ouvinte=ouvinte)
            except ErroDeChat as e:
                print(f"AVISO: chat indisponivel ({e}); seguindo sem chat.", file=sys.stderr)

    largura, altura = (960, 720) if args.windowed else tamanho_tela_cheia()

    janela = "PrimeSense"
    # sem isso, o backend GTK do highgui as vezes cria a janela mas nunca a
    # mapeia de verdade na tela (fica invisivel, apesar do processo estar
    # rodando e desenhando normalmente) quando o app e' lancado via SSH/nohup
    # nesse Unity/compiz — visto e confirmado na pratica 2026-09-11.
    cv2.startWindowThread()
    cv2.namedWindow(janela, cv2.WINDOW_NORMAL)

    # O pedido de tela cheia só é atendido pelo window manager depois que a
    # janela já tem conteúdo (mapeada na tela) — pedir antes do primeiro
    # imshow faz o WM ignorar o hint silenciosamente.
    primeiro_frame = True

    try:
        while True:
            cor = ler_color(color_stream)
            cor = cv2.resize(cor, (largura, altura), interpolation=cv2.INTER_LINEAR)

            if detector:
                detector.submit(cor.copy())
                desenhar_rostos(cor, detector.boxes())

            quadro = cor
            if ouvinte is not None:
                quadro = compor_hud_transparente(quadro, visual_som, ouvinte, canto=args.corner)
            if cliente_chat:
                quadro = desenhar_chat(quadro, cliente_chat.mensagens())
            cv2.imshow(janela, quadro)

            if primeiro_frame:
                if not args.windowed:
                    cv2.setWindowProperty(janela, cv2.WND_PROP_FULLSCREEN,
                                           cv2.WINDOW_FULLSCREEN)
                primeiro_frame = False

            tecla = cv2.waitKey(1) & 0xFF
            if tecla in (ord("q"), 27):  # 'q' ou ESC
                break
    finally:
        if detector:
            detector.parar()
        if cliente_chat:
            cliente_chat.parar()
        if ouvinte:
            ouvinte.parar()
        parar_sensor(color_stream)
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
