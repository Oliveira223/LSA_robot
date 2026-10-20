"""
painel_texto.py — janela de mensagens (transcricao da fala) desenhada por
cima do video, no canto superior direito.

Cada frase vira um balao de mensagem, a mais recente embaixo. Usa PIL pra
poder mostrar acentos (o cv2.putText nao renderiza "ã", "é", "ç"...).

O painel so e redesenhado (PIL) quando as mensagens ou o estado mudam; nos
outros quadros so faz o blend do overlay ja pronto em cima do video.
"""

import os

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

# primeira que existir: sem uma fonte TrueType o PIL cai na fonte padrao minuscula
# (e os acentos saem errados), entao ha varias opcoes
_FONTES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/opentype/urw-base35/NimbusSans-Regular.otf",
    "/usr/share/fonts/truetype/noto/NotoSansMono-Regular.ttf",
]
_FONTES_NEGRITO = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/opentype/urw-base35/NimbusSans-Bold.otf",
    "/usr/share/fonts/truetype/noto/NotoSansMono-Bold.ttf",
]
FONTE = next((f for f in _FONTES if os.path.exists(f)), _FONTES[0])
FONTE_NEGRITO = next((f for f in _FONTES_NEGRITO if os.path.exists(f)), _FONTES_NEGRITO[0])

# RGB
COR_HUD = (0, 220, 255)       # ciano, mesma paleta dos outros paineis
COR_TEXTO = (255, 255, 255)
COR_BALAO = (10, 40, 60, 245)
COR_BALAO_ROBO = (14, 60, 36, 245)
COR_BORDA_ROBO = (110, 255, 130)
COR_FUNDO = (8, 14, 24, 215)
COR_APAGADA = (190, 205, 215)
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
    def __init__(self, margem=20, altura_frac=0.42, largura_frac=0.28, titulo="CONVERSA"):
        self.margem, self.altura_frac, self.largura_frac = margem, altura_frac, largura_frac
        self.titulo = titulo
        self._chave = None
        self._soma = None     # overlay BGR pre-multiplicado pelo alpha (uint8)
        self._inv_alpha = None  # 255 - alpha, 3 canais (uint8)

    def _renderizar(self, pw, ph, mensagens, estado, cerebro):
        k = max(0.7, pw / 540.0)
        f_txt, f_tit = _fonte(FONTE, int(20 * k)), _fonte(FONTE_NEGRITO, int(15 * k))
        f_rot = _fonte(FONTE_NEGRITO, int(12 * k))
        pad, gap = int(14 * k), int(10 * k)
        raio = int(16 * k)
        img = Image.new("RGBA", (pw, ph), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)

        # corpo do painel: vidro escuro com cantos arredondados + faixa de brilho no topo
        d.rounded_rectangle([0, 0, pw - 1, ph - 1], radius=raio, fill=COR_FUNDO,
                            outline=COR_HUD + (150,), width=2)
        topo = pad + int(26 * k)
        faixa = Image.new("RGBA", (pw, topo), (0, 0, 0, 0))
        fd = ImageDraw.Draw(faixa)
        for yy in range(topo):
            fd.line([(0, yy), (pw, yy)], fill=COR_HUD + (int(55 * (1 - yy / topo)),))
        mascara = Image.new("L", (pw, ph), 0)
        ImageDraw.Draw(mascara).rounded_rectangle([1, 1, pw - 2, ph - 2], radius=raio, fill=255)
        corte = Image.new("RGBA", (pw, ph), (0, 0, 0, 0))
        corte.paste(faixa, (0, 0))
        img = Image.composite(Image.alpha_composite(img, corte), img, mascara)
        d = ImageDraw.Draw(img)

        # cabecalho: ponto de estado com halo + titulo + status a direita
        cor_pt = COR_OK if estado == "pronto" else (COR_ERRO if estado == "erro" else COR_APAGADA)
        cy = pad + int(9 * k)
        r = int(5 * k)
        d.ellipse([pad - r + r, cy - r - int(4 * k), pad + 3 * r, cy + r + int(4 * k)],
                  fill=cor_pt + (45,))
        d.ellipse([pad + r - r // 1 + r // 2, cy - r // 1, pad + r + r // 1 + r // 2, cy + r // 1],
                  fill=cor_pt + (255,))
        d.text((pad + int(22 * k), pad), self.titulo, font=f_tit, fill=COR_HUD + (255,))
        if cerebro is False:
            txt, cor_st = TEXTO_SEM_CEREBRO, COR_ERRO
        elif estado == "pronto":
            txt, cor_st = "ao vivo", COR_OK
        else:
            txt, cor_st = "", COR_APAGADA
        if txt:
            larg = d.textlength(txt, font=f_rot)
            d.text((pw - pad - larg, pad + int(3 * k)), txt, font=f_rot, fill=cor_st + (255,))
        d.line([(pad, topo), (pw - pad, topo)], fill=COR_HUD + (90,), width=1)

        area_h = ph - topo - 2 * pad
        largura_balao = pw - 2 * pad
        altura_linha = int(f_txt.size * 1.3)
        h_rot = int(f_rot.size * 1.5)
        barra = int(4 * k)

        if not mensagens:
            d.text((pad, topo + pad), TEXTOS_ESTADO.get(estado, ""), font=f_txt, fill=COR_APAGADA + (255,))
        else:
            # de baixo pra cima ate acabar o espaco; a mais antiga que couber
            # aparece mais apagada. Usuario a esquerda, robo a direita; o
            # balao tem a largura do texto (ate 88% do painel).
            maximo = int(largura_balao * 0.88) - 2 * pad - barra
            blocos, usado = [], 0
            for autor, texto in reversed(mensagens):
                linhas = _quebrar(d, texto, f_txt, maximo)[:5]
                larg = int(max(d.textlength(l, font=f_txt) for l in linhas)) + 2 * pad + barra
                larg = max(larg, int(d.textlength("VOCÊ", font=f_rot)) + 2 * pad + barra)
                h = len(linhas) * altura_linha + h_rot + 2 * int(pad * 0.6)
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
                d.rounded_rectangle([x, y, x + larg, y + h], radius=int(12 * k), fill=fundo,
                                    outline=borda + ((70 if mais_velha else 140),), width=1)
                # faixa de cor na lateral do balao (lado de fora: esq. usuario, dir. robo)
                bx = x + larg - barra - 1 if robo else x + 1
                d.rounded_rectangle([bx, y + int(8 * k), bx + barra, y + h - int(8 * k)],
                                    radius=barra // 2, fill=borda + ((110 if mais_velha else 255),))
                tx = x + pad + (0 if robo else barra)
                ty = y + int(pad * 0.6)
                d.text((tx, ty), "ROBÔ" if robo else "VOCÊ", font=f_rot,
                       fill=borda + ((140 if mais_velha else 255),))
                ty += h_rot
                cor = COR_APAGADA if (texto == "…" or mais_velha) else COR_TEXTO
                for linha in linhas:
                    d.text((tx, ty), linha, font=f_txt, fill=cor + (255,))
                    ty += altura_linha
                y += h + gap

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
