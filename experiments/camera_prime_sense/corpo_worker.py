"""
corpo_worker.py — processo auxiliar de deteccao de CORPO (pose) com MediaPipe.

Roda no ambiente isolado venv_mp (o mediapipe puxa o opencv-contrib 5.x, que
nao pode conviver com o opencv-python 4.x do app: o 5.x removeu o
cv2.dnn.readNetFromDarknet que o YOLO de rosto usa). O app (corpo.py) fala
com este processo por stdin/stdout:

  - ao carregar o modelo escreve a linha "PRONTO\\n";
  - pedido:  3 inteiros big-endian (largura, altura, timestamp_ms) + largura*altura*3
             bytes de imagem BGR;
  - resposta: uma linha JSON {"poses": [[[x, y, visibilidade], ...33 pontos], ...]},
             x e y normalizados (0-1) em relacao a imagem enviada.

Uso manual (a partir desta pasta):
    LD_LIBRARY_PATH=~/dev/libgles2-x86/usr/lib/x86_64-linux-gnu \\
        venv_mp/bin/python corpo_worker.py
"""

import json
import os
import struct
import sys

os.environ.setdefault("GLOG_minloglevel", "2")        # sem o barulho do mediapipe no stderr
os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "2")

import numpy as np

AQUI = os.path.dirname(os.path.abspath(__file__))
MODELO = os.environ.get("LSA_CORPO_MODELO") or os.path.join(AQUI, "model-weights", "pose_landmarker_full.task")
MAX_PESSOAS = int(os.environ.get("LSA_CORPO_MAX", "3"))
CONF = float(os.environ.get("LSA_CORPO_CONF", "0.6"))


def _ler_exato(f, n):
    buf = bytearray()
    while len(buf) < n:
        parte = f.read(n - len(buf))
        if not parte:
            return None
        buf += parte
    return bytes(buf)


def main():
    os.nice(10)   # o video/HUD do app tem prioridade
    import mediapipe as mp
    from mediapipe.tasks import python as mpt
    from mediapipe.tasks.python import vision

    opcoes = vision.PoseLandmarkerOptions(
        base_options=mpt.BaseOptions(model_asset_path=MODELO),
        running_mode=vision.RunningMode.VIDEO,       # rastreia entre quadros: mais estavel e barato
        num_poses=MAX_PESSOAS,
        min_pose_detection_confidence=CONF,
        min_pose_presence_confidence=CONF,
        min_tracking_confidence=CONF)
    entrada, saida = sys.stdin.buffer, sys.stdout.buffer
    ultimo_ts = -1
    with vision.PoseLandmarker.create_from_options(opcoes) as detector:
        saida.write(b"PRONTO\n")
        saida.flush()
        while True:
            cab = _ler_exato(entrada, 12)
            if cab is None:
                return
            w, h, ts = struct.unpack(">III", cab)
            dados = _ler_exato(entrada, w * h * 3)
            if dados is None:
                return
            bgr = np.frombuffer(dados, np.uint8).reshape(h, w, 3)
            rgb = np.ascontiguousarray(bgr[..., ::-1])
            ts = max(ts, ultimo_ts + 1)              # o mediapipe exige timestamps crescentes
            ultimo_ts = ts
            try:
                r = detector.detect_for_video(mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb), ts)
                poses = [[[round(p.x, 4), round(p.y, 4), round(p.visibility, 3)] for p in pose]
                         for pose in r.pose_landmarks]
            except Exception as e:                   # uma frame ruim nao derruba o worker
                print("[corpo] erro: %s" % e, file=sys.stderr, flush=True)
                poses = []
            saida.write((json.dumps({"poses": poses}) + "\n").encode("utf-8"))
            saida.flush()


if __name__ == "__main__":
    main()
