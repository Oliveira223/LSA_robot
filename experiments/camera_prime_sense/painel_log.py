"""
painel_log.py — feed de logs "hacker" desenhado por cima do video, na faixa
esquerda: texto monoespacado branco rolando, as linhas mais antigas esmaecendo.

As linhas sao REAIS: acompanha o log do proprio app (/tmp/camera-simples.log:
sensor, rosto, transcricao, conversa, profundidade...) e, a cada poucos
segundos, acrescenta uma linha de telemetria do sistema (CPU, memoria,
temperatura) lida de /proc e /sys. Nada e inventado.

Como o painel_texto.py: so redesenha (PIL) quando as linhas mudam; nos demais
quadros faz so o blend do overlay pronto em cima do video.
"""

import collections
import os
import re
import time

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

FONTES = [
    "/usr/share/fonts/truetype/noto/NotoSansMono-Regular.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
]
FONTES_NEGRITO = [
    "/usr/share/fonts/truetype/noto/NotoSansMono-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf",
]

BRANCO = (255, 255, 255)
AMARELO = (255, 214, 102)
VERMELHO = (255, 120, 110)
CIANO = (120, 230, 255)

LOG_PADRAO = "/tmp/camera-simples.log"
# 2026-10-02 19:33:02 INFO  [transcritor] texto...
_LINHA = re.compile(r"^\d{4}-\d\d-\d\d (\d\d:\d\d:\d\d) (\w+)\s+\[([^\]]+)\]\s*(.*)$")
_RUIDO = ("QFontDatabase", "Note that Qt", "PROFILE", "audio recebido")   # nao interessa mostrar
# nomes curtos pras tags (cabem na coluna)
_TAGS = {"profundidade": "depth", "transcritor": "stt", "conversa": "chat", "sensor": "sensor",
         "mic_vad": "mic", "saudacao": "greet", "app": "core"}
# o arecord, quando falha, despeja os parametros pedidos (CHANNELS: 2, RATE: 48000...)
_DUMP_ARECORD = re.compile(r"^(ACCESS|FORMAT|SUBFORMAT|SAMPLE_BITS|FRAME_BITS|CHANNELS|RATE|PERIOD_\w+|PERIODS|BUFFER_\w+|TICK_TIME)\b")
_DEPTH_RE = re.compile(r"depth: (\d+) quadros, (\d+) erros")


def _fonte(lista, tam):
    for caminho in lista:
        if os.path.exists(caminho):
            try:
                return ImageFont.truetype(caminho, tam)
            except OSError:
                pass
    return ImageFont.load_default()


class _Telemetria:
    """CPU (% desde a leitura anterior), memoria usada e temperatura do pacote."""

    def __init__(self):
        self._ult = self._cpu()

    @staticmethod
    def _cpu():
        try:
            with open("/proc/stat") as f:
                v = [int(x) for x in f.readline().split()[1:]]
            return sum(v), v[3] + v[4]          # total, ocioso (idle + iowait)
        except (OSError, ValueError, IndexError):
            return 0, 0

    def linha(self):
        tot, oc = self._cpu()
        d_tot, d_oc = tot - self._ult[0], oc - self._ult[1]
        self._ult = (tot, oc)
        cpu = 100.0 * (1 - d_oc / d_tot) if d_tot > 0 else 0.0
        mem = ""
        try:
            m = {}
            with open("/proc/meminfo") as f:
                for l in f:
                    k, v = l.split(":")
                    m[k] = int(v.split()[0])
            mem = " mem=%.1fG" % ((m["MemTotal"] - m["MemAvailable"]) / 1048576.0)
        except (OSError, ValueError, KeyError):
            pass
        temp = ""
        try:
            base = "/sys/class/thermal"
            for z in sorted(os.listdir(base)):
                if open(os.path.join(base, z, "type")).read().strip() == "x86_pkg_temp":
                    temp = " temp=%dC" % (int(open(os.path.join(base, z, "temp")).read()) // 1000)
                    break
        except (OSError, ValueError):
            pass
        return "cpu=%.0f%%%s%s" % (cpu, mem, temp)


class PainelLog:
    def __init__(self, caminho=LOG_PADRAO, x=24, topo_frac=0.14, largura_frac=0.29,
                 altura_frac=0.52, telemetria_s=6.0):
        self.caminho = caminho
        self.x, self.topo_frac = x, topo_frac
        self.largura_frac, self.altura_frac = largura_frac, altura_frac
        self.telemetria_s = telemetria_s
        self._linhas = collections.deque(maxlen=60)    # (hora, nivel, tag, texto)
        self._pos = None             # offset ja lido do arquivo
        self._t_leitura = 0.0
        self._t_tele = 0.0
        self._tele = _Telemetria()
        self._chave = None
        self._soma = None
        self._inv_alpha = None

    # -- entrada de linhas -------------------------------------------
    def _ler_arquivo(self):
        try:
            tam = os.path.getsize(self.caminho)
        except OSError:
            return
        if self._pos is None or tam < self._pos:       # 1a vez ou log recriado
            self._pos = max(0, tam - 6000)             # pre-preenche com o rabo do log
        if tam == self._pos:
            return
        try:
            with open(self.caminho, "rb") as f:
                f.seek(self._pos)
                dados = f.read(tam - self._pos)
        except OSError:
            return
        self._pos = tam
        for bruta in dados.decode("utf-8", errors="replace").splitlines():
            if any(r in bruta for r in _RUIDO):
                continue
            m = _LINHA.match(bruta)
            if m:
                hora, nivel, tag, texto = m.groups()
                if _DUMP_ARECORD.match(texto):
                    continue
                if tag == "profundidade":
                    # a cada 5 s, sempre igual: so vale mostrar se houver erro (o resumo
                    # de saude do sistema ja aparece na linha de telemetria)
                    d = _DEPTH_RE.search(texto)
                    if d and d.group(2) == "0":
                        continue
                self._linhas.append((hora, nivel, _TAGS.get(tag, tag), texto.strip()))

    def evento(self, tag, texto, nivel="INFO"):
        """Linha de evento vinda do proprio app (ex.: rosto novo/perdido)."""
        self._linhas.append((time.strftime("%H:%M:%S"), nivel, tag, texto))

    def _atualizar(self):
        agora = time.time()
        if agora - self._t_leitura >= 0.4:
            self._t_leitura = agora
            self._ler_arquivo()
        if agora - self._t_tele >= self.telemetria_s:
            self._t_tele = agora
            self._linhas.append((time.strftime("%H:%M:%S"), "INFO", "sys", self._tele.linha()))

    # -- desenho ------------------------------------------------------
    def _renderizar(self, pw, ph, linhas):
        k = max(0.8, pw / 560.0)
        f = _fonte(FONTES, int(16 * k))
        f_neg = _fonte(FONTES_NEGRITO, int(16 * k))
        alt = int(f.size * 1.45)
        img = Image.new("RGBA", (pw, ph), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        # faixa escura bem suave so pra dar leitura sobre video claro
        for xx in range(pw):
            a = int(105 * (1 - xx / pw) ** 1.4)
            d.line([(xx, 0), (xx, ph)], fill=(0, 0, 0, a))
        d.line([(0, 0), (0, ph)], fill=CIANO + (150,), width=2)

        cabeca = "SYSTEM LOG  ▮"
        d.text((int(10 * k), int(2 * k)), cabeca, font=f_neg, fill=CIANO + (255,))

        n = len(linhas)
        topo = alt + int(8 * k)
        for i, (hora, nivel, tag, texto) in enumerate(linhas):
            y = ph - (n - i) * alt - int(4 * k)
            if y < topo:
                continue
            pos = i / max(1, n - 1)                       # 0 = mais antiga, 1 = mais nova
            alfa = int(70 + 185 * pos ** 0.8)
            cor = AMARELO if nivel == "WARN" else VERMELHO if nivel in ("ERROR", "CRITICAL") else BRANCO
            pref = "%s %-8s" % (hora, "[" + tag[:6].upper() + "]")
            larg_pref = d.textlength(pref, font=f)
            d.text((int(10 * k), y), pref, font=f, fill=CIANO + (int(alfa * 0.75),))
            resto = texto
            maxw = pw - int(10 * k) - larg_pref - int(8 * k)
            while resto and d.textlength(resto, font=f) > maxw:
                resto = resto[:-1]
            if resto != texto:
                resto = resto[:-1] + "…"
            d.text((int(10 * k) + larg_pref, y), resto, font=f_neg if i == n - 1 else f,
                   fill=cor + (alfa,))

        rgba = np.asarray(img)
        alpha = rgba[..., 3:4].astype(np.uint16)
        bgr = rgba[..., [2, 1, 0]].astype(np.uint16)
        self._soma = np.ascontiguousarray((bgr * alpha // 255).astype(np.uint8))
        self._inv_alpha = np.ascontiguousarray(np.repeat(255 - alpha, 3, axis=2).astype(np.uint8))

    def desenhar(self, fundo):
        h, w = fundo.shape[:2]
        pw, ph = int(w * self.largura_frac), int(h * self.altura_frac)
        y0 = int(h * self.topo_frac)
        x0 = self.x
        if y0 + ph > h or x0 + pw > w:
            return fundo
        self._atualizar()
        # quantas linhas cabem: o painel mostra so as mais novas
        k = max(0.8, pw / 560.0)
        cabem = max(1, int((ph - int(40 * k)) / (int(16 * k) * 1.45)))
        linhas = tuple(list(self._linhas)[-cabem:])
        chave = (pw, ph, linhas)
        if chave != self._chave:
            self._renderizar(pw, ph, linhas)
            self._chave = chave
        regiao = fundo[y0:y0 + ph, x0:x0 + pw]
        fundo[y0:y0 + ph, x0:x0 + pw] = cv2.add(
            cv2.multiply(regiao, self._inv_alpha, scale=1.0 / 255.0), self._soma)
        return fundo
