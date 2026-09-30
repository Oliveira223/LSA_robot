"""Detectores de rosto leves (YuNet / SCRFD) rodando na GPU via TensorRT.

Sem pycuda (nao compila nessa Jetson): a memoria de GPU e' gerenciada direto
pelo libcudart via ctypes. O quadro da camera (640x480) entra SEM redimensionar
num tensor 640x640 (preenchido com preto embaixo), entao rostos pequenos
mantem a resolucao original — e' o que o YOLOv3 a 128 px perdia.

Uso:
    det = DetectorTRT("model-weights/yunet.engine", "yunet")
    caixas = det.detectar(frame_bgr)   # [(x, y, w, h, conf), ...] em px do frame
"""
import ctypes

import cv2
import numpy as np
import tensorrt as trt

_cudart = ctypes.CDLL("libcudart.so", mode=ctypes.RTLD_GLOBAL)
_H2D, _D2H = 1, 2


def _ck(err):
    if err != 0:
        raise RuntimeError("cudart erro %d" % err)


class DetectorTRT:
    LADO = 640

    def __init__(self, caminho_engine, tipo, conf=0.4, nms=0.4):
        assert tipo in ("yunet", "scrfd")
        self.tipo, self.conf, self.nms = tipo, conf, nms
        logger = trt.Logger(trt.Logger.ERROR)
        with open(caminho_engine, "rb") as f:
            self._eng = trt.Runtime(logger).deserialize_cuda_engine(f.read())
        self._ctx = self._eng.create_execution_context()
        self._bind = []      # ponteiros de GPU, na ordem dos bindings
        self._saidas = {}    # nome -> (idx, host array)
        self._idx_in = None
        for i in range(self._eng.num_bindings):
            shape = tuple(self._eng.get_binding_shape(i))
            n = int(np.prod(shape))
            ptr = ctypes.c_void_p()
            _ck(_cudart.cudaMalloc(ctypes.byref(ptr), n * 4))
            self._bind.append(ptr)
            if self._eng.binding_is_input(i):
                self._idx_in = i
            else:
                self._saidas[self._eng.get_binding_name(i)] = (i, np.empty(shape, np.float32))
        self._entrada = np.zeros((1, 3, self.LADO, self.LADO), np.float32)
        self._bindings_ptr = [p.value for p in self._bind]

    def _preprocessar(self, frame):
        h, w = frame.shape[:2]
        assert w <= self.LADO and h <= self.LADO, "frame maior que 640x640"
        if self.tipo == "yunet":
            img = frame.astype(np.float32)                      # BGR 0..255
        else:
            img = (frame[:, :, ::-1].astype(np.float32) - 127.5) / 128.0  # RGB normalizado
        self._entrada[:] = 0 if self.tipo == "yunet" else 0.0
        self._entrada[0, :, :h, :w] = img.transpose(2, 0, 1)

    def _inferir(self):
        _ck(_cudart.cudaMemcpy(self._bind[self._idx_in], self._entrada.ctypes.data_as(ctypes.c_void_p),
                               self._entrada.nbytes, _H2D))
        if not self._ctx.execute_v2(self._bindings_ptr):
            raise RuntimeError("TensorRT execute_v2 falhou")
        for (i, arr) in self._saidas.values():
            _ck(_cudart.cudaMemcpy(arr.ctypes.data_as(ctypes.c_void_p), self._bind[i], arr.nbytes, _D2H))

    def _decodificar(self):
        S = self._saidas
        caixas, confs = [], []
        for s, n in ((8, 80), (16, 40), (32, 20)):
            if self.tipo == "yunet":
                cls = S["cls_%d" % s][1][0, :, 0]
                obj = S["obj_%d" % s][1][0, :, 0]
                bb = S["bbox_%d" % s][1][0]
                score = np.sqrt(np.clip(cls, 0, 1) * np.clip(obj, 0, 1))
                ok = np.nonzero(score >= self.conf)[0]
                for k in ok:
                    r, c = divmod(int(k), n)
                    cx, cy = (c + bb[k, 0]) * s, (r + bb[k, 1]) * s
                    w, h = np.exp(bb[k, 2]) * s, np.exp(bb[k, 3]) * s
                    caixas.append([int(cx - w / 2), int(cy - h / 2), int(w), int(h)])
                    confs.append(float(score[k]))
            else:
                # out0..2 = scores (stride 8,16,32), out3..5 = bbox, 2 ancoras por celula
                j = {8: 0, 16: 1, 32: 2}[s]
                score = S["out%d" % j][1][0, :, 0]
                bb = S["out%d" % (j + 3)][1][0] * s
                ok = np.nonzero(score >= self.conf)[0]
                for k in ok:
                    cel = int(k) // 2
                    r, c = divmod(cel, n)
                    ax, ay = c * s, r * s
                    x0, y0, x1, y1 = ax - bb[k, 0], ay - bb[k, 1], ax + bb[k, 2], ay + bb[k, 3]
                    caixas.append([int(x0), int(y0), int(x1 - x0), int(y1 - y0)])
                    confs.append(float(score[k]))
        if not caixas:
            return []
        idx = cv2.dnn.NMSBoxes(caixas, confs, self.conf, self.nms)
        idx = np.array(idx).flatten() if len(idx) else []
        return [tuple(caixas[i]) + (confs[i],) for i in idx]

    def detectar(self, frame_bgr):
        self._preprocessar(frame_bgr)
        self._inferir()
        return self._decodificar()
