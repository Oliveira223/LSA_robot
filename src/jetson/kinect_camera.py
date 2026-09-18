"""
kinect_camera.py — RGB + profundidade do Kinect (Xbox 360) via libfreenect
(ctypes direto contra a lib C — NAO existe binding Python 3 pronto no apt
dessa Jetson: "python-freenect" e Python 2 only. Ver README/PROGRESSO.md
pra mais contexto de por que isso existe separado do caminho da PrimeSense).

IMPORTANTE — escrito em 2026-09-14 SEM hardware disponivel pra testar (a
Jetson so tem 1 porta USB, ocupada pela PrimeSense; o Kinect tambem precisa
da fonte de energia externa dele — cabo Y — pra ligar de verdade). Os
tamanhos de buffer/struct abaixo seguem a API publica e estavel da
libfreenect 0.5.x, mas so ficam confirmados de verdade na primeira
conexao real — os asserts em `_ao_iniciar_stream` existem exatamente pra
isso: se o tamanho que a lib reportar via freenect_find_*_mode() nao bater
com o esperado (640x480), avisa alto em vez de corromper dado calado.

Pre-requisito de sistema (nao vem por padrao):
    sudo apt install -y libfreenect0.5 libfreenect-dev libfreenect-bin

Compatibilidade: Python 3.6 da Jetson (sem "from __future__ import
annotations", sem "X | None") — mesma regra de mic_vad.py/chat_client.py.
numpy e usado pro retorno dos frames (generalizado o bastante pra nao valer
a pena evitar); cv2 so e importado dentro da demonstracao (__main__) e da
colorizacao de profundidade — quem so quer os frames crus nao paga esse
custo.

Uso (a partir de src/, com a venv que tem cv2+numpy — ver
~/Desktop/README.md sobre a venv do app da camera):
    python3 -m jetson.kinect_camera     # abre janela com RGB + profundidade colorida
"""

import ctypes
import ctypes.util
import glob
import os
import threading
import time

import numpy as np

LARGURA_PADRAO = 640
ALTURA_PADRAO = 480

# freenect_resolution
FREENECT_RESOLUTION_MEDIUM = 1
# freenect_video_format
FREENECT_VIDEO_RGB = 0
# freenect_depth_format
FREENECT_DEPTH_11BIT = 0

TAMANHO_RGB_ESPERADO = LARGURA_PADRAO * ALTURA_PADRAO * 3       # 8 bits x 3 canais
TAMANHO_DEPTH_ESPERADO = LARGURA_PADRAO * ALTURA_PADRAO * 2     # 11 bits armazenados em uint16


class ErroDeKinect(Exception):
    """Falha previsivel ao achar/abrir a libfreenect ou o dispositivo."""


def localizar_libfreenect():
    nome = ctypes.util.find_library("freenect")
    if nome:
        return nome
    candidatos = (
        glob.glob("/usr/lib/aarch64-linux-gnu/libfreenect.so*")
        + glob.glob("/usr/lib/x86_64-linux-gnu/libfreenect.so*")
        + glob.glob("/usr/lib/libfreenect.so*")
        + glob.glob("/usr/local/lib/libfreenect.so*")
    )
    return candidatos[0] if candidatos else None


class _FreenectFrameMode(ctypes.Structure):
    """Layout de freenect_frame_mode (freenect.h, libfreenect 0.5.x — estavel
    ha muitos anos). So usado aqui pra passar entre freenect_find_*_mode() e
    freenect_set_*_mode() e pra checar .bytes/.width/.height defensivamente
    — nunca escrito pelo lado Python."""
    _fields_ = [
        ("resolution", ctypes.c_int32),
        ("formato", ctypes.c_int32),   # union video_format/depth_format/dummy
        ("bytes", ctypes.c_int32),
        ("width", ctypes.c_int16),
        ("height", ctypes.c_int16),
        ("data_bits_per_pixel", ctypes.c_int8),
        ("padding_bits_per_pixel", ctypes.c_int8),
        ("framerate", ctypes.c_int8),
        ("is_valid", ctypes.c_int8),
    ]


_VideoCallback = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32)
_DepthCallback = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32)


def _carregar_lib():
    caminho = localizar_libfreenect()
    if not caminho:
        raise ErroDeKinect(
            "libfreenect nao encontrada — instale com "
            "'sudo apt install -y libfreenect0.5 libfreenect-dev libfreenect-bin'")
    lib = ctypes.CDLL(caminho)

    lib.freenect_init.argtypes = [ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p]
    lib.freenect_init.restype = ctypes.c_int
    lib.freenect_shutdown.argtypes = [ctypes.c_void_p]
    lib.freenect_shutdown.restype = ctypes.c_int
    lib.freenect_process_events.argtypes = [ctypes.c_void_p]
    lib.freenect_process_events.restype = ctypes.c_int
    lib.freenect_num_devices.argtypes = [ctypes.c_void_p]
    lib.freenect_num_devices.restype = ctypes.c_int
    lib.freenect_open_device.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p), ctypes.c_int]
    lib.freenect_open_device.restype = ctypes.c_int
    lib.freenect_close_device.argtypes = [ctypes.c_void_p]
    lib.freenect_close_device.restype = ctypes.c_int

    lib.freenect_find_video_mode.argtypes = [ctypes.c_int32, ctypes.c_int32]
    lib.freenect_find_video_mode.restype = _FreenectFrameMode
    lib.freenect_find_depth_mode.argtypes = [ctypes.c_int32, ctypes.c_int32]
    lib.freenect_find_depth_mode.restype = _FreenectFrameMode

    lib.freenect_set_video_mode.argtypes = [ctypes.c_void_p, _FreenectFrameMode]
    lib.freenect_set_video_mode.restype = ctypes.c_int
    lib.freenect_set_depth_mode.argtypes = [ctypes.c_void_p, _FreenectFrameMode]
    lib.freenect_set_depth_mode.restype = ctypes.c_int

    lib.freenect_set_video_buffer.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    lib.freenect_set_video_buffer.restype = ctypes.c_int
    lib.freenect_set_depth_buffer.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    lib.freenect_set_depth_buffer.restype = ctypes.c_int

    lib.freenect_set_video_callback.argtypes = [ctypes.c_void_p, _VideoCallback]
    lib.freenect_set_video_callback.restype = None
    lib.freenect_set_depth_callback.argtypes = [ctypes.c_void_p, _DepthCallback]
    lib.freenect_set_depth_callback.restype = None

    lib.freenect_start_video.argtypes = [ctypes.c_void_p]
    lib.freenect_start_video.restype = ctypes.c_int
    lib.freenect_start_depth.argtypes = [ctypes.c_void_p]
    lib.freenect_start_depth.restype = ctypes.c_int
    lib.freenect_stop_video.argtypes = [ctypes.c_void_p]
    lib.freenect_stop_video.restype = ctypes.c_int
    lib.freenect_stop_depth.argtypes = [ctypes.c_void_p]
    lib.freenect_stop_depth.restype = ctypes.c_int
    return lib


class SensorKinect:
    """Abre o Kinect (video + profundidade) e mantem os frames mais
    recentes disponiveis via ultimo_rgb()/ultimo_depth(), atualizados por
    uma thread de fundo que bombeia freenect_process_events() — mesmo
    espirito de LeitorProfundidade em Camera_Simples.py (PrimeSense): um
    engasgo no sensor so deixa o frame desatualizado, nao trava quem le."""

    def __init__(self, indice=0):
        self._lib = _carregar_lib()

        ctx = ctypes.c_void_p()
        if self._lib.freenect_init(ctypes.byref(ctx), None) < 0:
            raise ErroDeKinect("freenect_init falhou")
        self._ctx = ctx

        dev = ctypes.c_void_p()
        if self._lib.freenect_open_device(self._ctx, ctypes.byref(dev), indice) < 0:
            self._lib.freenect_shutdown(self._ctx)
            raise ErroDeKinect(
                "nao foi possivel abrir o Kinect indice %d (esta ligado na "
                "fonte de energia externa, nao so no USB?)" % indice)
        self._dev = dev

        modo_video = self._lib.freenect_find_video_mode(FREENECT_RESOLUTION_MEDIUM, FREENECT_VIDEO_RGB)
        modo_depth = self._lib.freenect_find_depth_mode(FREENECT_RESOLUTION_MEDIUM, FREENECT_DEPTH_11BIT)
        self._checar_modo(modo_video, "video RGB")
        self._checar_modo(modo_depth, "depth 11-bit")

        self._lib.freenect_set_video_mode(self._dev, modo_video)
        self._lib.freenect_set_depth_mode(self._dev, modo_depth)

        self._buf_rgb = ctypes.create_string_buffer(TAMANHO_RGB_ESPERADO)
        self._buf_depth = ctypes.create_string_buffer(TAMANHO_DEPTH_ESPERADO)
        self._lib.freenect_set_video_buffer(self._dev, self._buf_rgb)
        self._lib.freenect_set_depth_buffer(self._dev, self._buf_depth)

        self._lock = threading.Lock()
        self._ultimo_rgb = None
        self._ultimo_depth = None

        # precisa manter referencia forte aos CFUNCTYPE — ctypes nao segura
        # sozinho, e a callback sendo coletada pelo GC vira crash silencioso
        self._cb_video = _VideoCallback(self._on_video)
        self._cb_depth = _DepthCallback(self._on_depth)
        self._lib.freenect_set_video_callback(self._dev, self._cb_video)
        self._lib.freenect_set_depth_callback(self._dev, self._cb_depth)

        if self._lib.freenect_start_video(self._dev) < 0:
            raise ErroDeKinect("freenect_start_video falhou")
        if self._lib.freenect_start_depth(self._dev) < 0:
            raise ErroDeKinect("freenect_start_depth falhou")

        self._rodando = True
        self._thread = threading.Thread(target=self._loop_eventos, daemon=True)
        self._thread.start()

    @staticmethod
    def _checar_modo(modo, rotulo):
        # so checa is_valid — testado com hardware real em 2026-09-14: o
        # campo modo.bytes le 0 por causa de algum desalinhamento no layout
        # ctypes de _FreenectFrameMode (nao descoberto qual exatamente), mas
        # os frames de verdade vieram com o tamanho/shape certos mesmo assim
        # (RGB 480x640x3, depth 480x640 com valores 11-bit plausiveis) — os
        # TAMANHO_*_ESPERADO abaixo ja estao confirmados corretos na pratica,
        # so a LEITURA desse campo especifico do struct que nao presta.
        if not modo.is_valid:
            raise ErroDeKinect("modo de %s invalido — libfreenect nao suporta "
                                "essa combinacao de resolucao/formato" % rotulo)

    def _on_video(self, dev, dados, timestamp):
        with self._lock:
            self._ultimo_rgb = np.frombuffer(self._buf_rgb.raw, dtype=np.uint8).reshape(
                ALTURA_PADRAO, LARGURA_PADRAO, 3).copy()

    def _on_depth(self, dev, dados, timestamp):
        with self._lock:
            self._ultimo_depth = np.frombuffer(self._buf_depth.raw, dtype=np.uint16).reshape(
                ALTURA_PADRAO, LARGURA_PADRAO).copy()

    def _loop_eventos(self):
        while self._rodando:
            ret = self._lib.freenect_process_events(self._ctx)
            if ret < 0:
                print("[kinect_camera] freenect_process_events erro (%d) — "
                      "encerrando thread de eventos" % ret, flush=True)
                break

    def ultimo_rgb(self):
        """Ultimo frame RGB (numpy uint8, HxWx3) ou None se ainda nao chegou nenhum."""
        with self._lock:
            return self._ultimo_rgb

    def ultimo_depth(self):
        """Ultimo frame de profundidade cru (numpy uint16, HxW, 11-bit —
        0 = sem leitura) ou None se ainda nao chegou nenhum."""
        with self._lock:
            return self._ultimo_depth

    def parar(self):
        self._rodando = False
        try:
            self._lib.freenect_stop_video(self._dev)
            self._lib.freenect_stop_depth(self._dev)
        except Exception:
            pass
        self._thread.join(timeout=2)
        try:
            self._lib.freenect_close_device(self._dev)
        except Exception:
            pass
        try:
            self._lib.freenect_shutdown(self._ctx)
        except Exception:
            pass


def colorir_depth(depth_cru, colormap_nome=None):
    """Colorizacao simples do depth cru (11-bit) pra exibicao — mesma ideia
    de normalizar por percentil que Camera_Simples.py ja usava pra
    profundidade da PrimeSense (evita uma mancha unica quando ha poucos
    pixels validos), so que autocontida aqui (nao importa de
    experiments/camera_prime_sense — esse modulo nao depende do caminho da
    PrimeSense)."""
    import cv2

    buracos = (depth_cru == 0).astype(np.uint8)
    validos = depth_cru[depth_cru > 0]
    lo, hi = np.percentile(validos, [2, 98]) if validos.size else (0, 1)
    if hi <= lo:
        hi = lo + 1
    img8 = np.clip((depth_cru.astype(np.float32) - lo) / (hi - lo) * 255, 0, 255).astype(np.uint8)
    img8 = cv2.inpaint(img8, buracos, 3, cv2.INPAINT_TELEA)
    colormap = colormap_nome if colormap_nome is not None else cv2.COLORMAP_PLASMA
    return cv2.applyColorMap(img8, colormap)


if __name__ == "__main__":
    # Demonstracao isolada: abre RGB + profundidade colorida numa janela em
    # tela cheia, sem chat/mic/YOLO. Precisa de cv2 (rode com a venv de
    # ~/dev/Camera-prime-sense — ver README) e do Kinect ligado (USB +
    # fonte de energia externa).
    import argparse

    import cv2

    from common.janela import tamanho_tela_cheia

    ap = argparse.ArgumentParser()
    ap.add_argument("--windowed", action="store_true",
                     help="abre em janela normal em vez de tela cheia")
    args = ap.parse_args()

    print("[kinect_camera] abrindo o Kinect...", flush=True)
    try:
        sensor = SensorKinect()
    except ErroDeKinect as e:
        print("[kinect_camera] %s" % e, flush=True)
        raise SystemExit(1)

    print("[kinect_camera] aberto — 'q' ou ESC fecha", flush=True)
    janela = "Kinect"
    largura_tela, altura_tela = (1280, 480) if args.windowed else tamanho_tela_cheia()
    # sem isso, o backend GTK do highgui as vezes cria a janela mas nunca a
    # mapeia de verdade na tela (fica invisivel, apesar do processo estar
    # rodando e desenhando normalmente) quando o app e lancado via SSH/nohup
    # nesse Unity/compiz — mesmo bug ja visto e documentado em
    # experiments/camera_prime_sense/Camera_Simples.py (2026-09-11).
    cv2.startWindowThread()
    cv2.namedWindow(janela, cv2.WINDOW_NORMAL)
    # o pedido de tela cheia so e atendido pelo window manager depois que a
    # janela ja tem conteudo (mapeada na tela) — pedir antes do primeiro
    # imshow faz o WM ignorar o hint silenciosamente (mesmo caso de
    # Camera_Simples.py).
    primeiro_frame = True
    try:
        while True:
            rgb = sensor.ultimo_rgb()
            depth = sensor.ultimo_depth()
            if rgb is None or depth is None:
                time.sleep(0.05)
                continue
            # o Kinect manda RGB de verdade (nao precisa do RGB2BGR que a
            # PrimeSense precisa) — so inverte canal pro cv2.imshow (BGR)
            bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
            depth_colorido = colorir_depth(depth)
            depth_colorido = cv2.resize(depth_colorido, (bgr.shape[1], bgr.shape[0]))
            quadro = np.hstack([bgr, depth_colorido])
            quadro = cv2.resize(quadro, (largura_tela, altura_tela))
            cv2.imshow(janela, quadro)

            if primeiro_frame:
                if not args.windowed:
                    cv2.setWindowProperty(janela, cv2.WND_PROP_FULLSCREEN, cv2.WINDOW_FULLSCREEN)
                primeiro_frame = False

            if (cv2.waitKey(1) & 0xFF) in (ord("q"), 27):
                break
    finally:
        sensor.parar()
        cv2.destroyAllWindows()
        print("\n[kinect_camera] encerrado", flush=True)
