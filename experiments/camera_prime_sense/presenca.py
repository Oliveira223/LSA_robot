"""
presenca.py — o robo so responde a fala quando ha alguem na frente dele.

O Whisper entende qualquer conversa, inclusive a do laboratorio atras do robo; sem
isso ele responderia a todo mundo. Sem ninguem na faixa o robo tambem NAO FALA (o audio
das respostas e descartado em conversa_client.py). Aqui "ha alguem" = um rosto confirmado OU um corpo
(MediaPipe) a menos de `dist_max` metros (pela profundidade da PrimeSense). A presenca
dura `graca_s` depois do ultimo quadro com alguem, pra uma frase que termina quando a
pessoa vira o rosto ou sai de quadro ainda valer.

Se os detectores de rosto e de corpo estiverem os dois desligados (`vision off`) ou a
presenca estiver desligada (`presence off`), nao ha o que checar e a fala passa sempre.

    p = Presenca()
    p.atualizar(rostos=[2.7, None], corpos=[3.1], detectores_ligados=True)   # a cada quadro
    p.presente()      # o transcritor consulta quando a frase termina
"""

import time

import numpy as np


class Presenca:
    def __init__(self, ativo=True, dist_max=2.3, graca_s=4.0):
        self.ativo = ativo
        self.dist_max = dist_max      # m: so responde e fala com alguem a ate 2,3 m do Xtion
        self.graca_s = graca_s
        self._visto = 0.0             # ultima vez com alguem na faixa
        self._detectores = True
        self._mais_perto = None       # menor distancia vista no ultimo quadro (m) ou None

    def atualizar(self, rostos, corpos, detectores_ligados=True):
        """`rostos`/`corpos`: listas de distancias em metros (None = sem leitura de profundidade;
        conta como presente, porque nao da pra provar que esta longe)."""
        self._detectores = detectores_ligados
        validas = [d for d in list(rostos) + list(corpos) if d is not None]
        perto = [d for d in validas if d <= self.dist_max]
        sem_leitura = (len(rostos) + len(corpos)) > len(validas)
        self._mais_perto = min(validas) if validas else None
        if perto or sem_leitura:
            self._visto = time.time()

    def presente(self):
        """True se a fala deve ser aceita."""
        if not self.ativo or not self._detectores:
            return True
        return time.time() - self._visto <= self.graca_s

    def estado(self):
        """Texto curto pro `status`."""
        if not self.ativo:
            return "desligada (o robo responde a qualquer fala)"
        if not self._detectores:
            return "ligada, mas sem rosto/corpo ligados: nao ha como checar (fala passa sempre)"
        agora = "ALGUEM na frente" if self.presente() else "NINGUEM na frente (a fala e ignorada)"
        perto = "" if self._mais_perto is None else ", mais perto a %.1f m" % self._mais_perto
        return "ligada (ate %.1f m, tolerancia %.0f s): %s%s" % (self.dist_max, self.graca_s, agora, perto)


def profundidade_corpo_m(mapa, pose):
    """Distancia (m) de um corpo (33 pontos normalizados) pela profundidade no meio dos ombros;
    se os ombros nao aparecem, no meio dos quadris. None sem `mapa` ou sem pixels validos."""
    if mapa is None:
        return None
    H, W = mapa.shape[:2]
    for a, b in ((11, 12), (23, 24)):
        if pose[a][2] >= 0.5 and pose[b][2] >= 0.5:
            cx = int((pose[a][0] + pose[b][0]) / 2 * W)
            cy = int((pose[a][1] + pose[b][1]) / 2 * H)
            v = mapa[max(0, cy - 10):min(H, cy + 11), max(0, cx - 10):min(W, cx + 11)].ravel()
            v = v[(v >= 350) & (v <= 6000)]
            return float(np.median(v)) / 1000.0 if v.size >= 10 else None
    return None
