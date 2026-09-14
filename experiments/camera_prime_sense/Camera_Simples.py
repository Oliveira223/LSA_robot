#!/usr/bin/env python3
"""
Visualização simples da câmera PrimeSense: imagem RGB em tela cheia, com a
profundidade (colorizada) em miniatura num canto, e um bounding box verde
ao redor de rostos detectados (YOLOv3-face) na imagem RGB. Sem sidebar, sem
outros modos — só isso.

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
import threading
import time

import cv2
import numpy as np
from primesense import openni2

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
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
    openni2.initialize(localizar_openni2_redist())
    dev = openni2.Device.open_any()
    color_stream = dev.create_color_stream()
    depth_stream = dev.create_depth_stream()
    color_stream.start()
    depth_stream.start()
    return dev, color_stream, depth_stream


def parar_sensor(color_stream, depth_stream):
    for s in (color_stream, depth_stream):
        try:
            s.stop()
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


def ler_depth_colorido(depth_stream, colormap=cv2.COLORMAP_INFERNO):
    """Colorizacao com apelo visual/HUD (proposital, nao fidelidade metrica):
    normaliza por percentil (em vez de min/max puro) pra nao estourar o
    frame inteiro numa unica mancha quando ha poucos pixels validos (vidro,
    reflexo), e desenha um contorno tipo wireframe + linha de scan por cima
    pra dar uma cara mais "sci-fi" de visao robotica."""
    frame = depth_stream.read_frame()
    img = np.frombuffer(frame.get_buffer_as_uint16(), dtype=np.uint16)
    img = img.reshape(frame.height, frame.width)
    buracos = (img == 0).astype(np.uint8)  # pixels sem leitura

    validos = img[img > 0]
    lo, hi = np.percentile(validos, [2, 98]) if validos.size else (0, 1)
    if hi <= lo:
        hi = lo + 1
    img8 = np.clip((img.astype(np.float32) - lo) / (hi - lo) * 255, 0, 255).astype(np.uint8)
    img8 = cv2.inpaint(img8, buracos, 3, cv2.INPAINT_TELEA)

    colorido = cv2.applyColorMap(img8, colormap)

    # contorno tipo wireframe por cima (glow branco nas bordas de profundidade)
    bordas = cv2.dilate(cv2.Canny(img8, 40, 120), None, iterations=1)
    colorido[bordas > 0] = (255, 255, 255)

    # grade fina tipo HUD, bem sutil
    h, w = colorido.shape[:2]
    grade = colorido.copy()
    passo = 24
    for x in range(0, w, passo):
        cv2.line(grade, (x, 0), (x, h), (255, 255, 255), 1, cv2.LINE_AA)
    for y in range(0, h, passo):
        cv2.line(grade, (0, y), (w, y), (255, 255, 255), 1, cv2.LINE_AA)
    colorido = cv2.addWeighted(grade, 0.06, colorido, 0.94, 0)

    # linha de "scan" varrendo de cima a baixo, sincronizada pelo relogio
    y_scan = int(((time.time() % 3.0) / 3.0) * h)
    cv2.line(colorido, (0, y_scan), (w, y_scan), (255, 255, 255), 2, cv2.LINE_AA)

    return colorido


class LeitorProfundidade:
    """Le e coloriza a profundidade numa thread separada. Na pratica
    depth_stream.read_frame() as vezes trava/demora varios segundos nessa
    Jetson (visto e confirmado 2026-09-11) — rodando numa thread a parte, um
    engasgo na profundidade so deixa a miniatura desatualizada, sem travar o
    video principal (cor + rosto)."""

    def __init__(self, depth_stream):
        self._depth_stream = depth_stream
        self._lock = threading.Lock()
        self._ultimo = np.zeros((240, 320, 3), dtype=np.uint8)  # placeholder ate a 1a leitura
        self._rodando = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def ultimo_frame(self):
        with self._lock:
            return self._ultimo

    def parar(self):
        self._rodando = False
        self._thread.join(timeout=2)

    def _loop(self):
        while self._rodando:
            try:
                prof = ler_depth_colorido(self._depth_stream)
            except Exception:
                import traceback; traceback.print_exc()
                time.sleep(0.2)  # evita spin de CPU quando a leitura falha
                continue
            with self._lock:
                self._ultimo = prof


# ─── Composição da tela ─────────────────────────────────────────────────
def compor_picture_in_picture(fundo, mini, canto="br", margem=20, escala=0.28,
                               rotulo="DEPTH SCAN"):
    """Cola `mini` (a profundidade) pequena num canto de `fundo` (a cor,
    já no tamanho da tela), com moldura estilo HUD (cantos tipo mira +
    rótulo) em vez de uma borda simples."""
    cor_hud = (255, 220, 0)  # ciano tipo HUD, em BGR

    h, w = fundo.shape[:2]
    mw = int(w * escala)
    mh = int(mini.shape[0] * (mw / mini.shape[1]))
    mini = cv2.resize(mini, (mw, mh), interpolation=cv2.INTER_AREA)

    borda = 2
    mini = cv2.copyMakeBorder(mini, borda, borda, borda, borda,
                               cv2.BORDER_CONSTANT, value=cor_hud)
    mh, mw = mini.shape[:2]

    if canto == "tl":
        x, y = margem, margem
    elif canto == "tr":
        x, y = w - mw - margem, margem
    elif canto == "bl":
        x, y = margem, h - mh - margem
    else:  # "br"
        x, y = w - mw - margem, h - mh - margem

    saida = fundo.copy()
    saida[y:y + mh, x:x + mw] = mini

    # cantos tipo mira/HUD nos 4 vertices da miniatura
    tick = 14
    for (cx, cy, dx, dy) in [(x, y, 1, 1), (x + mw, y, -1, 1),
                              (x, y + mh, 1, -1), (x + mw, y + mh, -1, -1)]:
        cv2.line(saida, (cx, cy), (cx + dx * tick, cy), cor_hud, 2, cv2.LINE_AA)
        cv2.line(saida, (cx, cy), (cx, cy + dy * tick), cor_hud, 2, cv2.LINE_AA)

    cv2.putText(saida, rotulo, (x, y - 8), cv2.FONT_HERSHEY_SIMPLEX, 0.5,
                cor_hud, 1, cv2.LINE_AA)

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
                     help="canto onde a profundidade aparece (padrão: br)")
    ap.add_argument("--windowed", action="store_true",
                     help="abre em janela normal em vez de tela cheia")
    ap.add_argument("--no-faces", action="store_true",
                     help="desliga a deteccao de rosto (YOLOv3-face)")
    ap.add_argument("--face-size", type=int, default=160,
                     help="entrada da rede YOLO em pixels (padrao 160; menor = mais rapido)")
    ap.add_argument("--face-conf", type=float, default=0.5,
                     help="confianca minima da deteccao de rosto (padrao 0.5)")
    args = ap.parse_args()

    detector = None
    if not args.no_faces:
        if os.path.isfile(FACE_CFG) and os.path.isfile(FACE_WEIGHTS):
            detector = DetectorRosto(FACE_CFG, FACE_WEIGHTS,
                                      tamanho=args.face_size, confianca=args.face_conf)
        else:
            print(f"AVISO: modelo YOLO de rosto nao encontrado ({FACE_CFG} / "
                  f"{FACE_WEIGHTS}); seguindo sem deteccao de rosto.", file=sys.stderr)

    _dev, color_stream, depth_stream = iniciar_sensor()
    leitor_profundidade = LeitorProfundidade(depth_stream)

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

            profundidade = leitor_profundidade.ultimo_frame()

            quadro = compor_picture_in_picture(cor, profundidade, canto=args.corner)
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
        leitor_profundidade.parar()
        parar_sensor(color_stream, depth_stream)
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
