"""
painel_texto.py — janela de mensagens (transcricao da fala) desenhada por
cima do video, no canto superior direito.

Cada frase vira um balao de mensagem, a mais recente embaixo. Usa PIL pra
poder mostrar acentos (o cv2.putText nao renderiza "ã", "é", "ç"...).

O painel so e redesenhado (PIL) quando as mensagens ou o estado mudam; nos
outros quadros so faz o blend do overlay ja pronto em cima do video.
"""

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

FONTE = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
FONTE_NEGRITO = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"

# RGB
COR_HUD = (0, 220, 255)       # ciano, mesma paleta dos outros paineis
COR_TEXTO = (240, 248, 255)
COR_BALAO = (14, 52, 74, 215)
COR_BALAO_ROBO = (18, 70, 44, 215)
COR_BORDA_ROBO = (110, 255, 130)
COR_FUNDO = (0, 0, 0, 120)
COR_APAGADA = (150, 170, 180)
COR_OK = (110, 255, 130)
COR_ERRO = (255, 90, 80)

TEXTOS_ESTADO = {
    "carregando": "Carregando reconhecimento de voz…",
    "pronto": "Aguardando fala…",
    "erro": "Transcrição indisponível",
}
# aviso sob o titulo quando o cerebro (Jetson 1) esta fora do ar
TEXTO_SEM_CEREBRO = "sem conexão com o robô"


def _fonte(caminho, tam):
    try:
        return ImageFont.truetype(caminho, tam)
    except (OSError, IOError):
        return ImageFont.load_default()


def _quebrar(draw, texto, fonte, largura_max):
    linhas, atual = [], ""
    for palavra in texto.split():
        cand = (atual + " " + palavra).strip()
        if draw.textlength(cand, font=fonte) <= largura_max or not atual:
            atual = cand
        else:
            linhas.append(atual)
            atual = palavra
    if atual:
        linhas.append(atual)
    return linhas or [""]


class PainelTranscricao:
    def __init__(self, margem=20, altura_frac=0.5, largura_frac=0.32, titulo="TRANSCRIÇÃO"):
        self.margem, self.altura_frac, self.largura_frac = margem, altura_frac, largura_frac
        self.titulo = titulo
        self._chave = None
        self._soma = None     # overlay BGR pre-multiplicado pelo alpha (uint8)
        self._inv_alpha = None  # 255 - alpha, 3 canais (uint8)

    def _renderizar(self, pw, ph, mensagens, estado, cerebro):
        k = max(0.7, ph / 540.0)
        f_txt, f_tit = _fonte(FONTE, int(17 * k)), _fonte(FONTE_NEGRITO, int(14 * k))
        pad, gap = int(12 * k), int(8 * k)
        img = Image.new("RGBA", (pw, ph), COR_FUNDO)
        d = ImageDraw.Draw(img)

        # cabecalho: titulo + ponto de estado
        cor_pt = COR_OK if estado == "pronto" else (COR_ERRO if estado == "erro" else COR_APAGADA)
        d.ellipse([pad, pad + 3 * k, pad + 9 * k, pad + 12 * k], fill=cor_pt + (255,))
        d.text((pad + 16 * k, pad - 1), self.titulo, font=f_tit, fill=COR_HUD + (255,))
        if cerebro is False:
            larg = d.textlength(TEXTO_SEM_CEREBRO, font=f_tit)
            d.text((pw - pad - larg, pad - 1), TEXTO_SEM_CEREBRO, font=f_tit, fill=COR_ERRO + (255,))
        topo = pad + int(24 * k)
        d.line([(pad, topo), (pw - pad, topo)], fill=COR_HUD + (110,), width=1)

        area_h = ph - topo - pad
        largura_balao = pw - 2 * pad
        interno = largura_balao - 2 * pad
        altura_linha = int(f_txt.size * 1.3)

        if not mensagens:
            d.text((pad, topo + pad), TEXTOS_ESTADO.get(estado, ""), font=f_txt, fill=COR_APAGADA + (255,))
        else:
            # de baixo pra cima ate acabar o espaco; a mais antiga que couber
            # aparece mais apagada. Usuario a esquerda, robo a direita; o
            # balao tem a largura do texto (ate 85% do painel).
            maximo = int(largura_balao * 0.85) - 2 * pad
            blocos, usado = [], 0
            for autor, texto in reversed(mensagens):
                linhas = _quebrar(d, texto, f_txt, maximo)[:6]
                larg = int(max(d.textlength(l, font=f_txt) for l in linhas)) + 2 * pad
                h = len(linhas) * altura_linha + 2 * int(pad * 0.7)
                if usado + h > area_h:
                    break
                blocos.append((autor, texto, linhas, larg, h))
                usado += h + gap
            y = ph - pad - (usado - gap if blocos else 0)
            for i, (autor, texto, linhas, larg, h) in enumerate(reversed(blocos)):
                mais_velha = (i == 0 and len(blocos) > 2)
                robo = autor == "robo"
                base = COR_BALAO_ROBO if robo else COR_BALAO
                borda = COR_BORDA_ROBO if robo else COR_HUD
                fundo = base[:3] + ((150 if mais_velha else base[3]),)
                x = pw - pad - larg if robo else pad
                d.rounded_rectangle([x, y, x + larg, y + h], radius=int(10 * k), fill=fundo,
                                    outline=borda + ((90 if mais_velha else 200),), width=1)
                cor = COR_APAGADA if (texto == "…" or mais_velha) else COR_TEXTO
                ty = y + int(pad * 0.7)
                for linha in linhas:
                    d.text((x + pad, ty), linha, font=f_txt, fill=cor + (255,))
                    ty += altura_linha
                y += h + gap

        d.rectangle([0, 0, pw - 1, ph - 1], outline=COR_HUD + (255,), width=1)

        rgba = np.asarray(img)
        alpha = rgba[..., 3:4].astype(np.uint16)
        bgr = rgba[..., [2, 1, 0]].astype(np.uint16)
        self._soma = np.ascontiguousarray((bgr * alpha // 255).astype(np.uint8))
        self._inv_alpha = np.ascontiguousarray(np.repeat(255 - alpha, 3, axis=2).astype(np.uint8))

    def desenhar(self, fundo, mensagens, estado="pronto", cerebro=None):
        """`mensagens` = lista de (autor, texto), autor "usuario" ou "robo".
        `cerebro`: True/False = conexao com a Jetson 1; None = nao usa."""
        h, w = fundo.shape[:2]
        pw, ph = int(w * self.largura_frac), int(h * self.altura_frac)
        x0, y0 = w - pw - self.margem, self.margem
        if y0 + ph > h or x0 < 0:
            return fundo
        chave = (pw, ph, tuple(mensagens), estado, cerebro)
        if chave != self._chave:
            self._renderizar(pw, ph, mensagens, estado, cerebro)
            self._chave = chave
        regiao = fundo[y0:y0 + ph, x0:x0 + pw]
        # fundo*(1-a) + overlay*a, em uint8 com as rotinas NEON do OpenCV
        fundo[y0:y0 + ph, x0:x0 + pw] = cv2.add(
            cv2.multiply(regiao, self._inv_alpha, scale=1.0 / 255.0), self._soma)
        return fundo
