"""Utilitarios compartilhados pelos apps da PrimeSense (Run_Camera_Prime_Sense
e Camera_Simples): localizar o OpenNI2, escolher a resolucao do sensor e
detectar rostos com YOLO num processo separado.
"""
import glob
import multiprocessing as mp
import os
import time

import cv2
import numpy as np
try:
    from primesense import _openni2 as c_api
except ImportError:          # PC sem PrimeSense: so a webcam (ver WebcamCor)
    c_api = None

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Modelos YOLO de rosto, do mais leve pro mais pesado. O primeiro par que
# existir em disco e usado — basta colocar um yolov3-tiny-face (cfg em cfg/,
# pesos em model-weights/) que ele passa a ser preferido automaticamente.
MODELOS_ROSTO = [
    ("yolov3-tiny-face.cfg", "yolov3-tiny-face.weights"),
    ("yolov3-face.cfg", "yolov3-wider_16000.weights"),
]


def localizar_modelo_rosto():
    """Devolve (cfg, weights) do modelo mais leve disponivel, ou None."""
    for cfg, pesos in MODELOS_ROSTO:
        p_cfg = os.path.join(BASE_DIR, "cfg", cfg)
        p_pesos = os.path.join(BASE_DIR, "model-weights", pesos)
        if os.path.isfile(p_cfg) and os.path.isfile(p_pesos):
            return p_cfg, p_pesos
    return None


# ─── OpenNI2 ──────────────────────────────────────────────────────────
def _precarregar_libs_openni2_pc():
    """No PC sem root o OpenNI2 vem de um .deb extraido em ~/dev/openni2-x86; o
    libjpeg.so.8 que ele pede fica la tambem, fora do caminho do linker."""
    import ctypes
    base = os.path.expanduser("~/dev/openni2-x86/usr/lib/x86_64-linux-gnu")
    for nome in glob.glob(os.path.join(base, "libjpeg.so.8*")):
        try:
            ctypes.CDLL(nome, mode=ctypes.RTLD_GLOBAL)
        except OSError:
            pass
        break


_precarregar_libs_openni2_pc()


def localizar_openni2_redist():
    candidatos = [
        os.environ.get("OPENNI2_REDIST", ""),
        os.path.expanduser("~/dev/openni2-x86/usr/lib/x86_64-linux-gnu"),  # .deb extraido, sem sudo (PC)
        os.path.expanduser("~/dev/openni2-redist"),  # extraido do .deb, sem sudo (Jetson)
        "/usr/lib/aarch64-linux-gnu",
        "/usr/lib/x86_64-linux-gnu",
        "/usr/lib",
        "/usr/local/lib",
    ]
    for d in candidatos:
        if d and glob.glob(os.path.join(d, "libOpenNI2.so*")):
            return d
    for base in ("/usr/lib", "/usr/local/lib", "/opt"):
        hits = glob.glob(os.path.join(base, "**", "libOpenNI2.so*"), recursive=True)
        if hits:
            return os.path.dirname(hits[0])
    return ""  # deixa o OpenNI2 tentar o padrão do sistema


def _parse_res(texto):
    try:
        w, h = texto.lower().split("x")
        return int(w), int(h)
    except Exception:
        return None


def configurar_resolucao(stream, formato, res="auto", fps_min=15, max_largura=640):
    """Coloca `stream` na melhor resolucao suportada e devolve (w, h, fps).

    res: "auto" (maior area com fps >= fps_min e largura <= 640; a Carmine
    anuncia 1280x1024@30 mas entrega ~5 fps por limite do USB 2.0) ou
    "LxA" (ex.: "640x480"). Se nao der, mantem o modo padrao do sensor.
    O sensor e lido em runtime (get_sensor_info) pra nao chutar modos que a
    Carmine nao tem.
    """
    modos = [m for m in stream.get_sensor_info().videoModes if m.pixelFormat == formato]
    if not modos:
        return None

    pedido = None if res == "auto" else _parse_res(res)
    if pedido:
        cand = [m for m in modos if (m.resolutionX, m.resolutionY) == pedido]
        cand.sort(key=lambda m: -m.fps)
    else:
        cand = [m for m in modos if m.fps >= fps_min and m.resolutionX <= max_largura]
        cand.sort(key=lambda m: (-m.resolutionX * m.resolutionY, -m.fps))
    if not cand:
        return None

    m = cand[0]
    try:
        stream.set_video_mode(c_api.OniVideoMode(
            pixelFormat=m.pixelFormat, resolutionX=m.resolutionX,
            resolutionY=m.resolutionY, fps=m.fps))
    except Exception as e:
        print("AVISO: nao foi possivel usar %dx%d@%d (%s)" % (m.resolutionX, m.resolutionY, m.fps, e))
        return None
    return m.resolutionX, m.resolutionY, m.fps


def configurar_cor(stream, res="auto"):
    r = configurar_resolucao(stream, c_api.OniPixelFormat.ONI_PIXEL_FORMAT_RGB888, res)
    print("Cor:", "%dx%d@%dfps" % r if r else "modo padrao do sensor")
    return r


def configurar_depth(stream, res="640x480"):
    r = configurar_resolucao(stream, c_api.OniPixelFormat.ONI_PIXEL_FORMAT_DEPTH_1_MM, res)
    print("Depth:", "%dx%d@%dfps" % r if r else "modo padrao do sensor")
    return r


# ─── Webcam (PC sem PrimeSense) ───────────────────────────────────────
class WebcamCor:
    """Faz a webcam V4L2 (cv2.VideoCapture) passar por stream de cor: devolve
    quadros BGR com ler(). Sem profundidade, entao nao ha distancia dos rostos."""

    def __init__(self, indice=0, res="auto"):
        self._cap = cv2.VideoCapture(int(indice), cv2.CAP_V4L2)
        if not self._cap.isOpened():
            raise RuntimeError("nao consegui abrir a webcam %s" % indice)
        self._cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))  # 30 fps no USB 2.0
        largura, altura = _parse_res(res) if res != "auto" else (1280, 720)
        self._cap.set(cv2.CAP_PROP_FRAME_WIDTH, largura)
        self._cap.set(cv2.CAP_PROP_FRAME_HEIGHT, altura)
        self._cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        w = int(self._cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        h = int(self._cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        print("Cor: %dx%d@%dfps (webcam %s)" % (w, h, int(self._cap.get(cv2.CAP_PROP_FPS)), indice))

    def ler(self):
        ok, quadro = self._cap.read()
        if not ok:
            raise RuntimeError("a webcam parou de entregar quadros")
        return quadro

    def stop(self):
        self._cap.release()


def achar_webcam():
    """Indice da primeira webcam V4L2 que entrega imagem (o /dev/video1 de uma
    webcam costuma ser so de metadados), ou None."""
    for i in range(6):
        if not os.path.exists("/dev/video%d" % i):
            continue
        cap = cv2.VideoCapture(i, cv2.CAP_V4L2)
        ok = cap.isOpened() and cap.read()[0]
        cap.release()
        if ok:
            return i
    return None


# ─── Deteccao de rosto (YOLO, processo separado) ──────────────────────
# Confirmacao por um 2o detector (YuNet): o YOLO de 128 px confunde mao levantada e
# celular com rosto. O YuNet olha a ESTRUTURA do rosto (olhos/nariz/boca) e rejeita
# isso. Um candidato do YOLO so vira alvo se o YuNet confirmar no recorte, ou se o
# proprio YOLO tiver confianca muito alta. Sem o modelo YuNet, nao confirma nada
# (comportamento antigo). LSA_ROSTO_CONFIRMAR=0 desliga.
YUNET_ONNX = os.path.join(BASE_DIR, "model-weights", "face_detection_yunet_2023mar.onnx")
IOU_YUNET_MIN = float(os.environ.get("LSA_YUNET_IOU", "0.25"))   # casamento minimo YuNet x YOLO


def confirmacao_ativa():
    return os.environ.get("LSA_ROSTO_CONFIRMAR", "1") != "0" and os.path.isfile(YUNET_ONNX) \
        and hasattr(cv2, "FaceDetectorYN")


def _yunet_confirma(yunet, frame, caixa, conf_min):
    """Caixa (x, y, w, h) do rosto que o YuNet acha DENTRO de `caixa` (do YOLO), em
    coordenadas do quadro cheio; None se nao houver. So vale se a caixa do YuNet casa
    com a do YOLO (IoU) — um rosto vizinho a uma mao/celular nao salva a caixa que
    esta em cima do celular. A caixa devolvida e a do YuNet, que fica em cima do rosto
    de verdade (e assim se funde com o alvo existente em vez de virar um novo)."""
    H, W = frame.shape[:2]
    x, y, w, h = caixa
    mx, my = int(w * 0.35), int(h * 0.35)               # contexto em volta (testa, queixo, orelhas)
    x0, y0 = max(0, x - mx), max(0, y - my)
    x1, y1 = min(W, x + w + mx), min(H, y + h + my)
    if x1 - x0 < 8 or y1 - y0 < 8:
        return None
    recorte = frame[y0:y1, x0:x1]
    escala = max(1.0, 160.0 / max(recorte.shape[:2]))   # rosto pequeno: amplia o recorte
    if escala > 1.0:
        recorte = cv2.resize(recorte, None, fx=escala, fy=escala, interpolation=cv2.INTER_CUBIC)
    yunet.setInputSize((recorte.shape[1], recorte.shape[0]))
    yunet.setScoreThreshold(conf_min)
    _, rostos = yunet.detect(recorte)
    if rostos is None:
        return None
    melhor, iou_melhor = None, IOU_YUNET_MIN
    for r in rostos:
        fx, fy = x0 + r[0] / escala, y0 + r[1] / escala
        fw, fh = r[2] / escala, r[3] / escala
        iou = _iou((fx, fy, fw, fh), (x, y, w, h))
        if iou > iou_melhor:
            melhor, iou_melhor = (int(fx), int(fy), int(fw), int(fh)), iou
    return melhor


def _processo_detector(cfg, weights, tamanho, confianca, nms, fila_frame, fila_boxes):
    # `confianca` aqui e' o piso: o DetectorRosto decide quem vira trilha nova
    """Carrega a rede uma vez e processa sempre o frame mais recente.
    Processo (nao thread): o cv2.dnn desse OpenCV trava o loop principal
    quando dividido so por thread. Limita a 3 threads pra sobrar um nucleo pro
    video/GUI (a Jetson tem 4 nucleos)."""
    os.nice(10)  # o video/HUD tem prioridade; o rastreio cobre o intervalo entre deteccoes
    # 1 thread: a ~10 deteccoes/s uma inferencia (~60 ms) cabe no intervalo, e cada
    # thread a mais so soma CPU gasta esperando (medido no PC: 3 threads = ~2x a CPU).
    # Na Jetson (so 4 nucleos, sem limite de taxa) use LSA_YOLO_THREADS=3.
    try:
        cv2.setNumThreads(int(os.environ.get("LSA_YOLO_THREADS", "1")))
    except ValueError:
        cv2.setNumThreads(1)
    net = cv2.dnn.readNetFromDarknet(cfg, weights)
    net.setPreferableBackend(cv2.dnn.DNN_BACKEND_OPENCV)
    net.setPreferableTarget(cv2.dnn.DNN_TARGET_CPU)
    nomes = net.getLayerNames()
    saidas_net = [nomes[int(i) - 1] for i in net.getUnconnectedOutLayers().flatten()]

    yunet = None
    conf_alta = float(os.environ.get("LSA_ROSTO_CONF_ALTA", "0.97"))   # acima disso nao precisa de 2a opiniao
    conf_yunet = float(os.environ.get("LSA_YUNET_CONF", "0.30"))
    if confirmacao_ativa():
        try:
            yunet = cv2.FaceDetectorYN.create(YUNET_ONNX, "", (160, 160), conf_yunet, 0.3, 50)
        except Exception as e:
            print("[rosto] YuNet nao carregou (%s); sem confirmacao" % e, flush=True)
    t_log = 0.0

    # Sem limite o loop roda 1 inferencia atras da outra (~30/s, ~3 nucleos
    # ocupados o tempo todo no PC). O fluxo optico do DetectorRosto ja move as
    # caixas a cada quadro, entao ~10 deteccoes/s bastam: mesma qualidade de
    # rastreio com ~1/4 da CPU. LSA_YOLO_HZ muda (0 = sem limite).
    try:
        hz = float(os.environ.get("LSA_YOLO_HZ", "10"))
    except ValueError:
        hz = 10.0
    intervalo = 1.0 / hz if hz > 0 else 0.0
    t_ult = 0.0

    while True:
        espera = intervalo - (time.time() - t_ult)
        if espera > 0:
            time.sleep(espera)   # dorme ANTES do get(): pega o quadro mais recente
        item = fila_frame.get()
        if item is None:
            break
        t_ult = time.time()
        fid, frame, (largura, altura) = item  # frame ja vem reduzido pra tamanho x tamanho

        cheio = frame
        if frame.shape[0] != tamanho or frame.shape[1] != tamanho:     # quadro cheio: reduz aqui
            frame = cv2.resize(frame, (tamanho, tamanho), interpolation=cv2.INTER_AREA)
        blob = cv2.dnn.blobFromImage(frame, 1 / 255.0, (tamanho, tamanho), swapRB=True, crop=False)
        net.setInput(blob)
        saidas = net.forward(saidas_net)

        caixas, confs = [], []
        for saida in saidas:
            ok = saida[:, 5] >= confianca  # filtra em bloco (numpy) em vez de loop
            for det in saida[ok]:
                bw, bh = det[2] * largura, det[3] * altura
                caixas.append([int(det[0] * largura - bw / 2), int(det[1] * altura - bh / 2),
                               int(bw), int(bh)])
                confs.append(float(det[5]))

        resultado = []
        if caixas:
            indices = cv2.dnn.NMSBoxes(caixas, confs, confianca, nms)
            if len(indices):
                resultado = [tuple(caixas[i]) + (confs[i],) for i in np.array(indices).flatten()]
                if yunet is not None:
                    aceitos = []
                    for c in resultado:
                        if c[4] >= conf_alta:
                            aceitos.append(c)
                            continue
                        fx = _yunet_confirma(yunet, cheio, c[:4], conf_yunet)
                        if fx is not None:
                            aceitos.append(fx + (c[4],))        # caixa do YuNet, confianca do YOLO
                        elif c[4] >= 0.5 and time.time() - t_log > 2.0:   # so as fortes (as fracas nunca virariam alvo); sem inundar o log
                            t_log = time.time()
                            print("[rosto] candidato descartado (conf %.2f): o YuNet nao viu rosto" % c[4], flush=True)
                    resultado = aceitos

        try:
            while True:
                fila_boxes.get_nowait()
        except Exception:
            pass
        fila_boxes.put((fid, resultado))


def _processo_trt(engine, tipo, conf_min, fila_frame, fila_boxes):
    """Mesmo contrato do _processo_detector, mas com SCRFD/YuNet na GPU
    (trt_faces.DetectorTRT). Recebe o quadro cheio da camera e devolve
    (fid, [(x, y, w, h, conf)]) em px desse quadro. ~50 ms por quadro."""
    import sys
    sys.path.insert(0, BASE_DIR)
    from trt_faces import DetectorTRT
    det = DetectorTRT(engine, tipo, conf=conf_min)
    while True:
        item = fila_frame.get()
        if item is None:
            break
        fid, frame, _tam = item
        resultado = det.detectar(frame)
        try:
            while True:
                fila_boxes.get_nowait()
        except Exception:
            pass
        fila_boxes.put((fid, resultado))


def _iou(a, b):
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    ix = max(0, min(ax + aw, bx + bw) - max(ax, bx))
    iy = max(0, min(ay + ah, by + bh) - max(ay, by))
    inter = ix * iy
    uniao = aw * ah + bw * bh - inter
    return inter / uniao if uniao > 0 else 0.0


def _contida(a, b):
    """Fracao da menor das duas caixas que esta dentro da outra (1.0 = uma
    inteira dentro da outra). O IoU sozinho nao pega o caso de uma caixa
    pequena dentro de uma maior no mesmo rosto."""
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    ix = max(0, min(ax + aw, bx + bw) - max(ax, bx))
    iy = max(0, min(ay + ah, by + bh) - max(ay, by))
    menor = min(aw * ah, bw * bh)
    return (ix * iy) / menor if menor > 0 else 0.0


# Anti-"muitas caixas": confirmacoes seguidas pra um candidato virar alvo, e teto de alvos.
CONFIRMACOES = int(os.environ.get("LSA_ROSTO_CONFIRMACOES", "2"))
MAX_TRILHAS = int(os.environ.get("LSA_ROSTO_MAX_ALVOS", "4"))


def _mesmo_rosto(a, b):
    return _iou(a, b) > 0.15 or _contida(a, b) > 0.6


class DetectorRosto:
    """YOLO (lento, ~2/s) + rastreamento por fluxo optico Lucas-Kanade (barato,
    a cada frame) pra caixa acompanhar o rosto em tempo real.

    submit(frame) deve ser chamado a cada frame da camera: manda o frame pro
    YOLO (reduzido), e move as caixas existentes pelo movimento dos pontos
    dentro delas. Quando o YOLO responde, o resultado e' de um frame ~0.5s
    mais velho: os pontos da nova caixa sao seguidos desde aquele frame ate
    o atual (compensa a latencia) e a caixa e' mesclada com a trilha
    existente (casada por IoU). Trilhas sem confirmacao do YOLO expiram
    apos `validade` segundos. Cada trilha tem um id estavel (alvo 01, 02...).
    """

    def __init__(self, cfg, weights, tamanho=128, confianca=0.5, nms=0.4,
                 validade=8.0, mescla=0.5, conf_minima=0.2, engine=None, tipo_engine="scrfd"):
        ctx = mp.get_context("spawn")  # nao herda handle do OpenNI2 nem o X11
        self.tamanho = tamanho
        self.confianca = confianca      # p/ CRIAR uma trilha nova
        self.conf_minima = conf_minima  # p/ REconfirmar uma trilha que ja existe
        # a trilha so morre se o fluxo optico a perder (ver submit) ou se ficar
        # `validade` s sem nenhuma confirmacao do YOLO — antes eram 1.2 s, e o
        # YOLO (~1 deteccao/s) errando uma vez ja derrubava a caixa
        self.validade = validade
        self.mescla = mescla
        self._escala = 1.0
        self._fid = 0
        self._hist = {}          # fid -> gray reduzido (pra compensar latencia)
        self._gray_prev = None
        self._prox_id = 1
        self._trilhas = []       # dicts: id, box [x,y,w,h] float, pts, visto, nasc
        self._fila_frame = ctx.Queue(maxsize=1)
        self._fila_boxes = ctx.Queue(maxsize=1)
        self._trt = engine is not None
        self._confirmar = (not self._trt) and confirmacao_ativa()
        if self._confirmar:
            # com o YuNet confirmando, um rosto de verdade e reconfirmado varias vezes por
            # segundo; uma caixa que ficou 8 s sem confirmacao e quase sempre o fluxo optico
            # carregando um celular/mao. 2.5 s cobre perfil/olhar pra baixo sem prender a caixa.
            self.validade = min(self.validade, 2.5)
        if self._trt:
            # com TensorRT (~50 ms) ha confirmacao ~15x/s: nao precisa segurar a
            # trilha tanto tempo sem confirmar (some logo quando a pessoa sai)
            self.validade = min(validade, 3.0)
            self.conf_minima = max(conf_minima, 0.3)
            alvo = (_processo_trt, (engine, tipo_engine, min(confianca, self.conf_minima),
                                    self._fila_frame, self._fila_boxes))
        else:
            alvo = (_processo_detector, (cfg, weights, tamanho, min(confianca, conf_minima), nms,
                                         self._fila_frame, self._fila_boxes))
        self._proc = ctx.Process(target=alvo[0], args=alvo[1], daemon=True)
        self._proc.start()

    # -- fluxo optico ------------------------------------------------
    _LK = dict(winSize=(15, 15), maxLevel=2,
               criteria=(cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 10, 0.05))

    def _semear(self, gray, box):
        """Cantos rastreaveis dentro do miolo da caixa (coords da imagem reduzida)."""
        x, y, w, h = [v * self._escala for v in box]
        H, W = gray.shape[:2]
        x0, y0 = max(0, int(x + w * 0.15)), max(0, int(y + h * 0.15))
        x1, y1 = min(W, int(x + w * 0.85)), min(H, int(y + h * 0.85))
        if x1 - x0 < 8 or y1 - y0 < 8:
            return None
        # so no recorte: com mascara o OpenCV ainda calcula o mapa de cantos da
        # imagem inteira (~27 ms em 640x480) — no recorte sao ~1 ms
        pts = cv2.goodFeaturesToTrack(gray[y0:y1, x0:x1], 25, 0.01, 3)
        if pts is not None:
            pts += np.array([x0, y0], np.float32)
        return pts

    def _mover(self, g0, g1, pts):
        """Segue pts de g0 pra g1. Devolve (dx, dy em px da imagem reduzida, pts novos) ou None."""
        if pts is None or len(pts) < 4:
            return None
        p1, st, _ = cv2.calcOpticalFlowPyrLK(g0, g1, pts, None, **self._LK)
        bons = st.ravel() == 1
        if bons.sum() < 4:
            return None
        d = (p1[bons] - pts[bons]).reshape(-1, 2)
        dx, dy = np.median(d, axis=0)
        return float(dx), float(dy), p1[bons]

    # -- API ---------------------------------------------------------
    def submit(self, frame_bgr):
        altura, largura = frame_bgr.shape[:2]
        # fluxo optico na resolucao cheia da camera: rosto pequeno (~25 px) fica
        # com pontos de sobra pra rastrear (a 320 px sobravam ~12 px)
        self._escala = 1.0
        gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
        # o YOLO redimensiona pra tamanho x tamanho de qualquer jeito: reduzir aqui
        # manda ~50 KB pela fila em vez de ~900 KB por quadro
        if self._trt:
            envio = frame_bgr  # quadro cheio: os rostos pequenos mantem a resolucao
        elif self._confirmar:
            envio = frame_bgr  # o processo precisa do quadro cheio pra recortar e confirmar
        else:
            envio = cv2.resize(frame_bgr, (self.tamanho, self.tamanho), interpolation=cv2.INTER_AREA)

        self._fid += 1
        self._hist[self._fid] = gray
        for k in [k for k in self._hist if k < self._fid - 40]:
            del self._hist[k]
        if self._trt:
            # so serializa (~900 KB) quando o detector ja terminou o anterior
            if self._fila_frame.empty():
                try:
                    self._fila_frame.put_nowait((self._fid, envio, (largura, altura)))
                except Exception:
                    pass
        else:
            try:
                self._fila_frame.get_nowait()
            except Exception:
                pass
            try:
                self._fila_frame.put_nowait((self._fid, envio, (largura, altura)))
            except Exception:
                pass

        agora = time.time()
        # 1) resultado novo do YOLO?
        res = None
        try:
            while True:
                res = self._fila_boxes.get_nowait()
        except Exception:
            pass
        # 2) trilhas atuais andam com o fluxo optico
        if self._gray_prev is not None:
            for t in self._trilhas:
                mv = self._mover(self._gray_prev, gray, t["pts"])
                if mv:
                    dx, dy, t["pts"] = mv
                    # um rosto nao anda mais que ~1/3 do proprio tamanho num quadro; um salto
                    # maior e a mao (ou outro objeto) arrastando os pontos: ignora o movimento
                    if max(abs(dx), abs(dy)) > 0.33 * max(t["box"][2], t["box"][3]):
                        t["pts"] = None
                    else:
                        t["box"][0] += dx / self._escala
                        t["box"][1] += dy / self._escala
                if t["pts"] is None or len(t["pts"]) < 6:
                    t["pts"] = self._semear(gray, t["box"])
                # sem pontos pra seguir, a caixa nao anda mais: nao vale segurar por
                # muito tempo sem o YOLO confirmar
                t["cega"] = t["cega"] + 1 if t["pts"] is None else 0
        # 3) incorpora o resultado do YOLO
        if res is not None:
            self._incorporar(res, gray, agora)
        H, W = gray.shape[:2]
        mantidas = []
        for t in self._trilhas:
            if agora - t["visto"] >= self.validade:
                motivo = "sem confirmacao"
            elif t["cega"] >= 15:                                # ~0.5 s sem nada pra seguir
                motivo = "sem pontos"
            elif t["n"] < CONFIRMACOES and agora - t["nasc"] > 0.8:
                motivo = "nao confirmou"                         # candidato que nao se repetiu
            elif not self._dentro(t["box"], W, H):
                motivo = "saiu da imagem"
            else:
                mantidas.append(t)
                continue
            if os.environ.get("FACE_DEBUG"):
                print("trilha %s morreu (%s) idade %.1fs" % (t["id"], motivo, agora - t["nasc"]), flush=True)
        self._trilhas = self._sem_duplicatas(mantidas)
        self._gray_prev = gray

    @staticmethod
    def _sem_duplicatas(trilhas):
        """Duas trilhas no mesmo rosto (a antiga arrastada pela mao + a nova): fica a
        mais velha confirmada; a outra sai. Teto separado: ate MAX_TRILHAS alvos
        confirmados e 2 candidatos (os mais novos), pra uma mao varrendo a frente da
        camera nao encher a lista nem bloquear um rosto de verdade."""
        trilhas = sorted(trilhas, key=lambda t: (t["n"] < CONFIRMACOES, t["nasc"]))
        fica = []
        for t in trilhas:
            if all(_iou(t["box"], o["box"]) < 0.25 and _contida(t["box"], o["box"]) < 0.5 for o in fica):
                fica.append(t)
        confirmadas = [t for t in fica if t["n"] >= CONFIRMACOES][:MAX_TRILHAS]
        candidatas = sorted((t for t in fica if t["n"] < CONFIRMACOES),
                            key=lambda t: t["nasc"], reverse=True)[:2]
        return confirmadas + candidatas

    @staticmethod
    def _dentro(box, W, H):
        """Falso quando o centro da caixa saiu da imagem (pessoa saiu de cena)."""
        cx, cy = box[0] + box[2] / 2, box[1] + box[3] / 2
        return 0 <= cx < W and 0 <= cy < H

    def _colado_em_alvo(self, box):
        """Trilha confirmada a menos de 1 tamanho de rosto do centro de `box` (ou None): uma
        deteccao ali so pode ser o celular/mao ao lado do rosto, nao outra pessoa."""
        cx, cy = box[0] + box[2] / 2, box[1] + box[3] / 2
        for t in self._trilhas:
            if t["n"] < CONFIRMACOES:
                continue
            tx, ty = t["box"][0] + t["box"][2] / 2, t["box"][1] + t["box"][3] / 2
            if ((cx - tx) ** 2 + (cy - ty) ** 2) ** 0.5 < max(t["box"][2], t["box"][3]):
                return t
        return None

    def descartar(self, tid):
        """Remove o alvo `tid` (ex.: a profundidade mudou de repente: ha um objeto na frente)."""
        antes = len(self._trilhas)
        self._trilhas = [t for t in self._trilhas if t["id"] != tid]
        return len(self._trilhas) != antes

    def _incorporar(self, res, gray_agora, agora):
        fid, caixas = res
        g_velho = self._hist.get(fid)
        for (x, y, w, h, conf) in caixas:
            box = [float(x), float(y), float(w), float(h)]
            # leva a deteccao (de um frame velho) ate o frame atual
            if g_velho is not None:
                pts = self._semear(g_velho, box)
                mv = self._mover(g_velho, gray_agora, pts)
                if mv:
                    box[0] += mv[0]
                    box[1] += mv[1]
            par = max(self._trilhas, key=lambda t: max(_iou(t["box"], box), _contida(t["box"], box)),
                      default=None)
            # candidato ainda nao confirmado so conta como "visto de novo" se a caixa ficou
            # praticamente no mesmo lugar (IoU alto); alvo ja confirmado e mais tolerante
            if par is not None and _mesmo_rosto(par["box"], box) and (
                    par["n"] >= CONFIRMACOES or _iou(par["box"], box) > 0.4):
                # trilha existente: aceita ate confianca baixa (rosto de perfil,
                # borrado, mao na frente...) — so nao deixa a trilha morrer
                m = self.mescla
                par["box"] = [m * o + (1 - m) * n for o, n in zip(par["box"], box)]
                par["visto"] = agora
                par["pts"] = self._semear(gray_agora, par["box"])
                par["cega"] = 0
                par["n"] += 1
                if par["id"] is None and par["n"] >= CONFIRMACOES:
                    par["id"] = self._prox_id
                    self._prox_id += 1
            elif conf >= self.confianca:
                colado = self._colado_em_alvo(box)
                if colado is not None:
                    # deteccao (ja confirmada pelo YuNet) colada num alvo que nao e
                    # reconfirmado ha um tempo: a caixa foi arrastada por um objeto;
                    # puxa de volta pro rosto. Se foi confirmado agora, e o celular: ignora.
                    if agora - colado["visto"] > 0.6:
                        colado["box"] = box
                        colado["visto"] = agora
                        colado["pts"] = self._semear(gray_agora, box)
                        colado["cega"] = 0
                    continue
                if os.environ.get("FACE_DEBUG"):
                    print("trilha %d nasceu conf %.2f box %s" % (self._prox_id, conf, [int(v) for v in box]), flush=True)
                # candidato: so ganha id (e aparece em alvos()) depois de CONFIRMACOES
                # deteccoes seguidas no mesmo lugar (sem atalho por confianca alta)
                novo = {"id": None, "box": box, "visto": agora, "nasc": agora, "cega": 0, "n": 1,
                        "pts": self._semear(gray_agora, box)}
                if CONFIRMACOES <= 1:
                    novo["id"] = self._prox_id
                    self._prox_id += 1
                self._trilhas.append(novo)

    def alvos(self):
        """[(id, (x, y, w, h), idade_s)] no tamanho do frame da camera."""
        agora = time.time()
        return [(t["id"], tuple(int(v) for v in t["box"]), agora - t["nasc"])
                for t in self._trilhas if t["id"] is not None]

    def boxes(self):
        return [b for (_i, b, _a) in self.alvos()]

    def parar(self):
        try:
            self._fila_frame.put_nowait(None)
        except Exception:
            pass
        self._proc.join(timeout=3)
        if self._proc.is_alive():
            self._proc.terminate()


ENGINES_TRT = [("scrfd_500m.engine", "scrfd"), ("yunet.engine", "yunet")]


def localizar_engine_trt():
    for nome, tipo in ENGINES_TRT:
        p = os.path.join(BASE_DIR, "model-weights", nome)
        if os.path.isfile(p):
            return p, tipo
    return None


def criar_detector_rosto(tamanho=128, confianca=0.5):
    """DetectorRosto com o melhor modelo disponivel: SCRFD/YuNet na GPU
    (TensorRT) se houver motor em model-weights/ e o tensorrt importar; senao
    o YOLOv3 na CPU; senao None (com aviso)."""
    trt_eng = localizar_engine_trt()
    if trt_eng is not None:
        try:
            import tensorrt  # noqa: F401
            import trt_faces  # noqa: F401  (falha cedo se o cudart nao carregar)
            print("Rosto: %s (TensorRT, GPU)" % os.path.basename(trt_eng[0]))
            return DetectorRosto(None, None, confianca=confianca, engine=trt_eng[0], tipo_engine=trt_eng[1])
        except Exception as e:
            print("AVISO: TensorRT indisponivel (%s); caindo pro YOLO na CPU." % e)
    modelo = localizar_modelo_rosto()
    if modelo is None:
        print("AVISO: modelo YOLO de rosto nao encontrado em cfg/ e model-weights/; "
              "seguindo sem deteccao de rosto.")
        return None
    print("YOLO rosto:", os.path.basename(modelo[1]), "entrada", tamanho)
    return DetectorRosto(modelo[0], modelo[1], tamanho=tamanho, confianca=confianca)


def desenhar_rostos(img, caixas):
    for (x, y, w, h) in caixas:
        cv2.rectangle(img, (x, y), (x + w, y + h), (0, 255, 0), 2)
        cv2.putText(img, "rosto", (x, max(12, y - 8)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)


# ─── HUD "hacker" ─────────────────────────────────────────────────────
HUD_CIANO = (255, 220, 0)    # BGR
HUD_VERDE = (90, 255, 60)
HUD_VERM = (60, 60, 255)
HUD_AMARELO = (0, 200, 255)

# faixas de distancia (m) -> cor da caixa do rosto
DIST_PERTO_M = 1.0    # abaixo disso: vermelho
DIST_MEDIA_M = 2.0    # ate aqui: amarelo; acima: verde


def cor_por_distancia(d):
    """Cor (BGR) da caixa conforme a distancia em metros; None se sem leitura."""
    if not d:
        return None
    if d < DIST_PERTO_M:
        return HUD_VERM
    if d < DIST_MEDIA_M:
        return HUD_AMARELO
    return HUD_VERDE


def _colchetes(img, x0, y0, x1, y1, tam, cor, esp):
    for (cx, cy, dx, dy) in ((x0, y0, 1, 1), (x1, y0, -1, 1), (x0, y1, 1, -1), (x1, y1, -1, -1)):
        cv2.line(img, (cx, cy), (cx + dx * tam, cy), cor, esp, cv2.LINE_AA)
        cv2.line(img, (cx, cy), (cx, cy + dy * tam), cor, esp, cv2.LINE_AA)


def _texto(img, txt, pos, escala, cor):
    cv2.putText(img, txt, pos, cv2.FONT_HERSHEY_SIMPLEX, escala, (0, 0, 0), 3, cv2.LINE_AA)
    cv2.putText(img, txt, pos, cv2.FONT_HERSHEY_SIMPLEX, escala, cor, 1, cv2.LINE_AA)


def _texto_grande(img, txt, pos, escala, cor):
    cv2.putText(img, txt, pos, cv2.FONT_HERSHEY_DUPLEX, escala, (0, 0, 0), 4, cv2.LINE_AA)
    cv2.putText(img, txt, pos, cv2.FONT_HERSHEY_DUPLEX, escala, cor, 2, cv2.LINE_AA)


def profundidade_mediana_mm(depth, caixa):
    """Mediana (mm) da profundidade no miolo (40%) de `caixa` (x, y, w, h, coords do mapa
    de profundidade); None se nao ha pixels validos. Sem suavizacao: serve pra pegar
    mudancas bruscas (objeto passando na frente)."""
    H, W = depth.shape[:2]
    x, y, w, h = caixa
    cx, cy = x + w / 2, y + h / 2
    x0, x1 = int(max(0, cx - w * 0.2)), int(min(W, cx + w * 0.2))
    y0, y1 = int(max(0, cy - h * 0.2)), int(min(H, cy + h * 0.2))
    v = depth[y0:y1, x0:x1].ravel()
    v = v[(v >= 350) & (v <= 6000)]
    return float(np.median(v)) if v.size >= 10 else None


class MedidorDistancia:
    """Distancia (m) de cada alvo a partir do mapa de profundidade (mm)
    registrado na imagem de cor. Mede o miolo da caixa (40%) pela mediana
    dos pixels validos e suaviza por alvo (media exponencial)."""

    def __init__(self, alfa=0.4, minimo_mm=350, maximo_mm=6000):
        self.alfa, self.minimo, self.maximo = alfa, minimo_mm, maximo_mm
        self._ema = {}

    def medir(self, depth, alvos):
        """`alvos` = [(id, (x, y, w, h))] em coordenadas da imagem de profundidade."""
        H, W = depth.shape[:2]
        saida = {}
        for aid, (x, y, w, h) in alvos:
            cx, cy = x + w / 2, y + h / 2
            x0, x1 = int(max(0, cx - w * 0.2)), int(min(W, cx + w * 0.2))
            y0, y1 = int(max(0, cy - h * 0.2)), int(min(H, cy + h * 0.2))
            v = depth[y0:y1, x0:x1].ravel()
            v = v[(v >= self.minimo) & (v <= self.maximo)]
            if v.size < 10:
                saida[aid] = self._ema.get(aid) and self._ema[aid] / 1000.0
                continue
            mm = float(np.median(v))
            ant = self._ema.get(aid)
            mm = mm if ant is None else ant + self.alfa * (mm - ant)
            self._ema[aid] = mm
            saida[aid] = mm / 1000.0
        for aid in list(self._ema):
            if aid not in saida:
                del self._ema[aid]
        return saida


def desenhar_alvos_hud(img, alvos, distancias=None):
    """Mira estilo HUD em cada alvo: colchetes, cruz central, linha de
    varredura, id, coordenadas e tempo de 'lock'. `alvos` = detector.alvos().
    `distancias` (opcional) = {id_do_alvo: metros ou None}."""
    t = time.time()
    distancias = distancias or {}
    H, W = img.shape[:2]
    for (aid, (x, y, w, h), idade) in alvos:
        x0, y0 = max(0, x), max(0, y)
        x1, y1 = min(W - 1, x + w), min(H - 1, y + h)
        if x1 - x0 < 10 or y1 - y0 < 10:
            continue
        lock = idade > 0.8
        cor = HUD_VERDE if lock else HUD_CIANO
        cor = cor_por_distancia(distancias.get(aid)) or cor
        tam = max(10, int(min(x1 - x0, y1 - y0) * 0.22))

        # moldura fina + colchetes grossos (pulsam levemente)
        cv2.rectangle(img, (x0, y0), (x1, y1), cor, 1, cv2.LINE_AA)
        pulso = int(3 * (0.5 + 0.5 * np.sin(t * 6)))
        _colchetes(img, x0 - pulso, y0 - pulso, x1 + pulso, y1 + pulso, tam, cor, 2)

        # cruz central
        cx, cy = (x0 + x1) // 2, (y0 + y1) // 2
        cv2.line(img, (cx - 8, cy), (cx + 8, cy), cor, 1, cv2.LINE_AA)
        cv2.line(img, (cx, cy - 8), (cx, cy + 8), cor, 1, cv2.LINE_AA)

        # linha de varredura vertical subindo/descendo pela caixa
        ys = y0 + int((0.5 + 0.5 * np.sin(t * 3 + aid)) * (y1 - y0))
        sobre = img[ys:ys + 1, x0:x1]
        if sobre.size:
            img[ys:ys + 1, x0:x1] = cv2.addWeighted(sobre, 0.3, np.full_like(sobre, cor), 0.7, 0)

        # rotulos
        _texto(img, "ROSTO %02d  %s" % (aid, "LOCK" if lock else "ACQ.."),
               (x0, max(16, y0 - 22)), 0.5, cor)
        if aid in distancias:
            d = distancias[aid]
            txt = "%.2f m" % d if d else "-- m"
            (tw, th), _ = cv2.getTextSize(txt, cv2.FONT_HERSHEY_DUPLEX, 0.9, 2)
            _texto_grande(img, txt, (x1 - tw, min(H - 8, y1 + th + 10)), 0.9, cor)
        _texto(img, "X:%04d Y:%04d  %dx%d" % (cx, cy, x1 - x0, y1 - y0),
               (x0, max(30, y0 - 6)), 0.4, cor)


class HudTela:
    """Camada de HUD da tela toda: faixas translucidas (topo/rodape) com o
    nome do robo, status e FPS, scanlines leves e colchetes nos cantos. O
    video continua ocupando a tela inteira — as faixas so escurecem um
    pouco as bordas."""

    NOME = "C-2GELSA"

    def __init__(self):
        self._t_ant = time.time()
        self._fps = 0.0
        self._scan = None
        self._t0 = time.time()

    def _montar_camadas(self, img):
        """Pre-calcula (uma vez por resolucao) o que sera subtraido do video:
        scanlines + degrade escuro no topo e no rodape. Depois e' um unico
        cv2.subtract por quadro."""
        H, W = img.shape[:2]
        sub = np.zeros_like(img)
        sub[::4] = 22
        alt_top, alt_bot = int(H * 0.14), int(H * 0.08)
        top = np.linspace(110, 0, alt_top, dtype=np.float32)[:, None, None]
        bot = np.linspace(0, 100, alt_bot, dtype=np.float32)[:, None, None]
        sub[:alt_top] = np.clip(sub[:alt_top] + top, 0, 255).astype(np.uint8)
        sub[H - alt_bot:] = np.clip(sub[H - alt_bot:] + bot, 0, 255).astype(np.uint8)
        self._scan = sub

    def desenhar(self, img, n_alvos, deteccao_ativa=True):
        agora = time.time()
        dt = agora - self._t_ant
        self._t_ant = agora
        if dt > 0:
            self._fps = 0.9 * self._fps + 0.1 * (1.0 / dt)
        H, W = img.shape[:2]
        k = max(0.6, H / 1080.0)   # escala dos textos/tracos com a resolucao

        if self._scan is None or self._scan.shape != img.shape:
            self._montar_camadas(img)
        cv2.subtract(img, self._scan, dst=img)

        # faixa de varredura que desce
        y = int((agora * 0.25 % 1.0) * H)
        faixa = img[y:y + 3]
        if faixa.size:
            img[y:y + 3] = cv2.addWeighted(faixa, 0.85, np.full_like(faixa, HUD_CIANO), 0.15, 0)

        m = int(20 * k)
        _colchetes(img, m, m, W - m, H - m, int(44 * k), HUD_CIANO, max(2, int(3 * k)))

        # ── cabecalho: nome do robo ────────────────────────────────────
        esp_nome = max(2, int(3 * k))
        xt, yt = m + int(22 * k), m + int(58 * k)
        cv2.rectangle(img, (xt - int(14 * k), yt - int(40 * k)),
                      (xt - int(8 * k), yt + int(10 * k)), HUD_CIANO, -1)   # barra de destaque
        cv2.putText(img, self.NOME, (xt, yt), cv2.FONT_HERSHEY_DUPLEX, 1.7 * k,
                    (0, 0, 0), esp_nome + 4, cv2.LINE_AA)
        cv2.putText(img, self.NOME, (xt, yt), cv2.FONT_HERSHEY_DUPLEX, 1.7 * k,
                    (255, 255, 255), esp_nome, cv2.LINE_AA)

        # ── rodape: status, alvos, FPS ─────────────────────────────────
        yb = H - m - int(22 * k)
        xb = m + int(64 * k)
        piscando = int(agora * 2) % 2 == 0
        if deteccao_ativa:
            cor_pt = HUD_VERDE if n_alvos else HUD_CIANO
            txt_st = "ROSTO DETECTADO" if n_alvos else "PROCURANDO ROSTOS"
        else:
            cor_pt, txt_st = HUD_VERM, "DETECCAO OFF"
        if piscando or not deteccao_ativa:
            cv2.circle(img, (xb + int(8 * k), yb - int(8 * k)), int(8 * k), cor_pt, -1, cv2.LINE_AA)
        _texto(img, txt_st, (xb + int(28 * k), yb), 0.7 * k, cor_pt)
        info = "ROSTOS %02d    FPS %02d" % (n_alvos, self._fps)
        (iw, _), _ = cv2.getTextSize(info, cv2.FONT_HERSHEY_SIMPLEX, 0.7 * k, 1)
        _texto(img, info, (W - m - int(64 * k) - iw, yb), 0.7 * k, HUD_CIANO)
        return img
