"""
corpo.py — deteccao de corpo (pose, 33 pontos) com MediaPipe, desenhada em estilo HUD.

O MediaPipe vive num ambiente separado (venv_mp) porque exige o opencv-contrib 5.x,
que quebraria o YOLO de rosto do app. Aqui fica so o lado do app: uma thread manda
~10 quadros/s pro corpo_worker.py (processo) e guarda a ultima resposta.

    corpo = DetectorCorpo()          # None-safe: se o worker nao subir, poses() devolve []
    corpo.submit(frame_bgr)          # a cada quadro da camera (nao bloqueia)
    desenhar_corpos(img, corpo.poses())

Configuracao por ambiente:
  LSA_CORPO_HZ   quadros por segundo enviados (padrao 15; cada resposta leva ~50 ms, o teto e ~20)
  LSA_CORPO_RES  largura do quadro enviado (padrao 640 = nativa da camera; a 480 uma pessoa pequena/longe nao e detectada)
"""

import collections
import json
import os
import struct
import subprocess
import threading
import time

import cv2
import numpy as np

AQUI = os.path.dirname(os.path.abspath(__file__))
PY_MP = os.path.join(AQUI, "venv_mp", "bin", "python")
WORKER = os.path.join(AQUI, "corpo_worker.py")
MODELO = os.path.join(AQUI, "model-weights", "pose_landmarker_full.task")
# libGLESv2 que o mediapipe pede e que nao vem instalada: extraida de um .deb sem root
LIB_GLES = os.path.expanduser("~/dev/libgles2-x86/usr/lib/x86_64-linux-gnu")

IDADE_MAX_S = 0.6        # poses mais velhas que isso nao sao desenhadas (pessoa saiu / worker parou)

# ligacoes do esqueleto do MediaPipe (33 pontos), SEM o rosto (pontos 0-10): o rosto
# ja e marcado pelo detector de rostos
LIGACOES = [
    (11, 12), (11, 23), (12, 24), (23, 24),                                          # tronco
    (11, 13), (13, 15), (15, 17), (15, 19), (15, 21), (17, 19),                      # braco esq.
    (12, 14), (14, 16), (16, 18), (16, 20), (16, 22), (18, 20),                      # braco dir.
    (23, 25), (25, 27), (27, 29), (29, 31), (27, 31),                                # perna esq.
    (24, 26), (26, 28), (28, 30), (30, 32), (28, 32),                                # perna dir.
]


def disponivel():
    return os.path.isfile(PY_MP) and os.path.isfile(WORKER) and os.path.isfile(MODELO)


class DetectorCorpo:
    def __init__(self, hz=None, largura=None):
        self.hz = float(hz or os.environ.get("LSA_CORPO_HZ") or 15)
        self.largura = int(largura or os.environ.get("LSA_CORPO_RES") or 640)
        self._lock = threading.Lock()
        self._frame = None            # ultimo quadro (ja reduzido) esperando envio
        self._poses = []
        self._t_poses = 0.0
        self._hist = collections.deque(maxlen=4)   # ultimos resultados (tinha pose?) pra nao piscar
        self._exib = []        # poses como desenhadas (suavizadas a cada quadro de video)
        self._t_exib = 0.0
        self._rodando = True
        self._proc = None
        self.pronto = False
        self.erro = None
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    # -- lado do app (chamado a cada quadro) -------------------------
    def submit(self, frame_bgr):
        """Guarda o quadro mais recente; a thread manda quando der (descarta os intermediarios)."""
        h, w = frame_bgr.shape[:2]
        esc = self.largura / float(w)
        pequeno = cv2.resize(frame_bgr, (self.largura, int(round(h * esc))), interpolation=cv2.INTER_AREA)
        with self._lock:
            self._frame = pequeno

    def poses(self):
        """[ndarray (33, 3)] com (x, y, visibilidade), x/y normalizados 0-1 na imagem da camera.

        Chamado a cada quadro de video (~30/s) enquanto o MediaPipe responde ~15/s: as poses
        devolvidas deslizam ate a ultima resposta em vez de pular, e o tremor com a pessoa
        parada e atenuado (constante de tempo adaptativa, no estilo do filtro 1-euro: parado
        = suave, em movimento = segue rapido)."""
        agora = time.time()
        with self._lock:
            if agora - self._t_poses > IDADE_MAX_S:
                self._exib = []
                return []
            # estavel: so mostra se 3 dos ultimos 4 resultados tinham pose (um falso positivo
            # de 1 quadro, ou uma falha de 1 quadro, nao faz o esqueleto piscar)
            if sum(self._hist) < min(3, self._hist.maxlen) and len(self._hist) >= 3:
                self._exib = []
                return []
            alvos = self._poses
        dt = min(0.2, max(0.0, agora - self._t_exib)) if self._t_exib else 0.0
        self._t_exib = agora
        novas = []
        for alvo in alvos:
            ant = self._casar(alvo)
            if ant is None or dt == 0.0:
                novas.append(alvo.copy())              # pessoa nova (ou 1o quadro): sem deslizar
                continue
            delta = alvo[:, :2] - ant[:, :2]
            dist = np.sqrt((delta ** 2).sum(axis=1))
            vel = np.clip(dist / 0.02, 0.0, 1.0)       # 0 = parado, 1 = andando
            tau = 0.10 * (1.0 - vel) + 0.03 * vel      # s
            alfa = (1.0 - np.exp(-dt / tau))[:, None]
            suave = ant.copy()
            suave[:, :2] = ant[:, :2] + alfa * delta
            suave[:, 2] = alvo[:, 2]                   # a visibilidade vem direto do modelo
            novas.append(suave)
        self._exib = novas
        return [p.copy() for p in novas]

    def _casar(self, alvo):
        """Pose exibida que corresponde a `alvo` (mesma pessoa), pelo centro; None se nao ha."""
        if not self._exib:
            return None
        c = alvo[:, :2].mean(axis=0)
        melhor, dmin = None, 0.15                      # mais longe que ~15% da imagem = outra pessoa
        for p in self._exib:
            d = float(np.linalg.norm(p[:, :2].mean(axis=0) - c))
            if d < dmin:
                melhor, dmin = p, d
        return melhor

    def parar(self):
        self._rodando = False
        if self._proc is not None:
            try:
                self._proc.stdin.close()
                self._proc.terminate()
            except Exception:
                pass

    # -- thread ------------------------------------------------------
    def _subir(self):
        env = dict(os.environ)
        env["LD_LIBRARY_PATH"] = ":".join(p for p in (LIB_GLES, env.get("LD_LIBRARY_PATH", "")) if p)
        self._proc = subprocess.Popen([PY_MP, WORKER], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                      stderr=subprocess.DEVNULL, env=env, cwd=AQUI)
        if self._proc.stdout.readline().strip() != b"PRONTO":
            raise RuntimeError("o worker de corpo nao disse PRONTO (veja corpo_worker.py)")

    def _loop(self):
        try:
            self._subir()
        except Exception as e:
            self.erro = str(e)
            print("[corpo] indisponivel: %s" % e, flush=True)
            return
        self.pronto = True
        print("[corpo] pronto (MediaPipe pose, %.0f quadros/s)" % self.hz, flush=True)
        t0 = time.time()
        proximo = 0.0
        try:
            while self._rodando:
                espera = proximo - time.time()
                if espera > 0:
                    time.sleep(espera)
                with self._lock:
                    frame, self._frame = self._frame, None
                if frame is None:
                    time.sleep(0.01)
                    continue
                proximo = time.time() + 1.0 / self.hz
                h, w = frame.shape[:2]
                self._proc.stdin.write(struct.pack(">III", w, h, int((time.time() - t0) * 1000)) + frame.tobytes())
                self._proc.stdin.flush()
                linha = self._proc.stdout.readline()
                if not linha:
                    raise RuntimeError("o worker de corpo encerrou")
                poses = [np.array(p, dtype=np.float32) for p in json.loads(linha.decode("utf-8")).get("poses", [])]
                with self._lock:
                    self._poses, self._t_poses = poses, time.time()
                    self._hist.append(bool(poses))
        except Exception as e:
            if self._rodando:
                self.erro = str(e)
                print("[corpo] parou: %s" % e, flush=True)
            with self._lock:
                self._poses = []


# ─── desenho (estilo HUD do app) ──────────────────────────────────────
COR_OSSO = (255, 220, 0)       # BGR: ciano, a mesma paleta dos outros paineis
COR_PONTO = (255, 255, 255)
VIS_MIN = 0.5


def desenhar_corpos(img, poses):
    """Esqueleto ciano com juntas brancas (sem rosto e sem rotulo)."""
    H, W = img.shape[:2]
    k = max(1.0, H / 540.0)
    for pose in poses:
        pts = [(int(x * W), int(y * H)) if v >= VIS_MIN else None for (x, y, v) in pose]
        for a, b in LIGACOES:
            if pts[a] is not None and pts[b] is not None:
                cv2.line(img, pts[a], pts[b], (0, 0, 0), int(4 * k), cv2.LINE_AA)       # contorno
                cv2.line(img, pts[a], pts[b], COR_OSSO, int(2 * k), cv2.LINE_AA)
        for i, p in enumerate(pts):
            if p is not None and i > 10:                                              # sem os pontinhos do rosto
                cv2.circle(img, p, int(4 * k), (0, 0, 0), -1, cv2.LINE_AA)
                cv2.circle(img, p, int(2.5 * k), COR_PONTO, -1, cv2.LINE_AA)
    return img
