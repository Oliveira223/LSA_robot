"""
mic_mock.py — fonte de audio SIMULADA, mesma interface publica de
jetson.mic_vad.OuvinteVAD (estado(), amostras_recentes(), proxima_fala(),
parar()) — sem nenhum hardware real por tras.

Pra que serve: manter o grafico de onda (VisualizadorSom, em
experiments/camera_prime_sense/Camera_Simples.py) "vivo" — bonito, chama
atencao de quem passa — mesmo quando o mic real esta indisponivel/instavel
(PrimeSense e Kinect os dois deram trabalho de USB nessa Jetson em
2026-09-14) ou quando so se quer demonstrar o app sem depender de sensor
nenhum (ex.: modo "vitrine", ver Camera_Simples.py --vitrine).

IMPORTANTE: proxima_fala() sempre devolve None — modo mock e SO visual.
Nunca manda "frase" nenhuma pro chat_client mandar pro servidor — mandar
ruido sintetico pra transcrever so geraria lixo/confusao. Se quiser chat de
verdade, precisa de um mic de verdade (OuvinteVAD ou OuvinteKinect).

Compatibilidade: Python 3.6 da Jetson.

Uso (a partir de src/):
    python3 -m jetson.mic_mock     # so imprime o nivel simulado
"""

import array
import math
import random
import struct
import threading
import time
from collections import deque

TAXA = 16000
LARGURA = 2
AMOSTRAS_POR_CICLO = 512   # tamanho de "chunk" simulado, mesma ordem de grandeza dos reais

LIMIAR_FALA = 1200
HISTORICO_AUDIO_S = 2.0


class OuvinteMock:
    """Simula alguem falando periodicamente: por `fracao_falando` de cada
    `ciclo_s`, o nivel sobe (RMS alto, tom + ruido) como se estivesse
    "OUVINDO..."; o resto do tempo fica num chiado baixo de fundo."""

    def __init__(self, limiar_fala=LIMIAR_FALA, ciclo_s=6.0, fracao_falando=0.35):
        self._limiar = limiar_fala
        self._ciclo_s = ciclo_s
        self._fracao_falando = fracao_falando
        self._t0 = time.time()

        n_chunks_historico = max(1, int(HISTORICO_AUDIO_S * TAXA / AMOSTRAS_POR_CICLO))
        self._historico_audio = deque(maxlen=n_chunks_historico)
        self._estado_lock = threading.Lock()
        self._nivel_atual = 0
        self._gravando_atual = False

        self._rodando = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def _loop(self):
        while self._rodando:
            t = time.time() - self._t0
            falando = (t % self._ciclo_s) < (self._ciclo_s * self._fracao_falando)
            amplitude = (2200 + 1400 * math.sin(t * 5.0)) if falando else 150

            amostras = []
            for i in range(AMOSTRAS_POR_CICLO):
                valor = amplitude * math.sin((t * TAXA + i) * 0.35) + random.uniform(-90, 90)
                amostras.append(int(max(-32000, min(32000, valor))))
            pedaco = struct.pack("<%dh" % AMOSTRAS_POR_CICLO, *amostras)
            nivel = int(abs(amplitude))

            with self._estado_lock:
                self._nivel_atual = nivel
                self._gravando_atual = falando
                self._historico_audio.append(pedaco)

            time.sleep(AMOSTRAS_POR_CICLO / float(TAXA))

    def estado(self):
        """Mesma assinatura de OuvinteVAD.estado() — `vivo` sempre True
        (nao existe hardware pra "cair" no modo mock)."""
        with self._estado_lock:
            return self._nivel_atual, self._limiar, self._gravando_atual, True

    def amostras_recentes(self):
        with self._estado_lock:
            pedacos = list(self._historico_audio)
        amostras = array.array("h")
        for pedaco in pedacos:
            amostras.frombytes(pedaco)
        return amostras

    def proxima_fala(self, timeout=None):
        """Sempre None — modo mock nao gera "frases" pra mandar pro
        servidor (so alimenta o grafico visual)."""
        if timeout:
            time.sleep(timeout)
        return None

    def parar(self):
        self._rodando = False
        self._thread.join(timeout=2)


if __name__ == "__main__":
    print("[mic_mock] simulando... (Ctrl-C sai)", flush=True)
    ouvinte = OuvinteMock()
    try:
        while True:
            nivel, limiar, gravando, vivo = ouvinte.estado()
            print("[mic_mock] nivel=%d limiar=%d gravando=%s" % (nivel, limiar, gravando),
                  flush=True)
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        ouvinte.parar()
        print("\n[mic_mock] encerrado", flush=True)
