"""
audio_prep.py — pre-processamento do audio do mic antes do Vosk.

Converte os pedacos do OuvinteVAD (S16_LE 48 kHz estereo) em PCM S16_LE 16 kHz
mono, melhorando o que o reconhecedor recebe:
  1. passa-baixa FIR (corte 7 kHz) ANTES de decimar 48k -> 16k, pra o chiado
     acima de 8 kHz nao virar aliasing na banda da voz;
  2. passa-alta de 100 Hz (Butterworth 2a ordem), tira ronco de motor/ventilador
     e o offset DC;
  3. ganho automatico (AGC): levanta quem fala baixo ou longe do mic ate um
     nivel alvo, com teto pra nao amplificar so ruido.

Stateful: os filtros guardam o estado entre pedacos da MESMA frase (sem
descontinuidade na emenda). Chamar resetar() no comeco de cada frase.

Configuracao por ambiente (opcional):
  LSA_AGC_ALVO   RMS alvo (0-32767) do sinal de 16 kHz   (padrao 2500)
  LSA_AGC_MAX    ganho maximo do AGC                     (padrao 8; 1 desliga)

Compatibilidade: Python 3.6 da Jetson (sem "X | None").
"""

import os

import numpy as np
from scipy import signal

TAXA_ENTRADA = 48000
TAXA_SAIDA = 16000
DECIMACAO = TAXA_ENTRADA // TAXA_SAIDA

AGC_ALVO = float(os.environ.get("LSA_AGC_ALVO", "2500"))
AGC_MAX = float(os.environ.get("LSA_AGC_MAX", "8"))
AGC_RUIDO_MIN = 150.0      # RMS abaixo disso e tratado como silencio: nao puxa ganho
AGC_DECAIMENTO = 0.97      # por pedaco de ~50 ms: o envelope cai devagar (~1,5 s)
AGC_LIBERA = 0.15          # quanto o ganho sobe por pedaco (sobe devagar, desce na hora)

# (scipy 0.19 da Jetson nao aceita fs=: frequencias normalizadas pela de Nyquist)
# projetados uma vez so; os filtros sao lineares, so o estado (zi) e por instancia
_FIR_PB = signal.firwin(63, 7000.0 / (TAXA_ENTRADA / 2.0)).astype(np.float64)
_SOS_PA = signal.butter(2, 100.0 / (TAXA_SAIDA / 2.0), btype="highpass", output="sos")


class PreProcessador:
    def __init__(self, agc=True):
        self._agc = agc and AGC_MAX > 1.0
        self.resetar()

    def resetar(self):
        self._zi_fir = np.zeros(len(_FIR_PB) - 1)
        self._zi_pa = np.zeros((_SOS_PA.shape[0], 2))
        self._envelope = 0.0
        self._ganho = None          # None: o 1o pedaco da frase ja entra no ganho alvo
        self._resto = np.zeros(0)   # amostras de 48k que sobraram da decimacao

    def processar(self, dados):
        """bytes S16_LE 48 kHz estereo -> bytes S16_LE 16 kHz mono."""
        est = np.frombuffer(dados, dtype=np.int16)
        if est.size < 2:
            return b""
        est = est[: est.size // 2 * 2].reshape(-1, 2)
        mono = est.astype(np.float64).mean(axis=1)

        mono = np.concatenate([self._resto, mono])
        n = mono.size // DECIMACAO * DECIMACAO
        self._resto = mono[n:]
        mono = mono[:n]
        if n == 0:
            return b""

        filtrado, self._zi_fir = signal.lfilter(_FIR_PB, 1.0, mono, zi=self._zi_fir)
        x = filtrado[::DECIMACAO]
        x, self._zi_pa = signal.sosfilt(_SOS_PA, x, zi=self._zi_pa)

        if self._agc:
            x = x * self._atualizar_ganho(x)
        return np.clip(x, -32768, 32767).astype(np.int16).tobytes()

    def _atualizar_ganho(self, x):
        rms = float(np.sqrt(np.mean(x * x))) if x.size else 0.0
        self._envelope = max(rms, self._envelope * AGC_DECAIMENTO)
        if self._envelope < AGC_RUIDO_MIN:
            alvo = 1.0                       # so ruido/silencio: nao amplifica
        else:
            alvo = min(max(AGC_ALVO / self._envelope, 1.0), AGC_MAX)
        if self._ganho is None or alvo < self._ganho:
            self._ganho = alvo               # 1o pedaco, ou mais alto que o esperado: na hora
        else:
            self._ganho += (alvo - self._ganho) * AGC_LIBERA
        return self._ganho
