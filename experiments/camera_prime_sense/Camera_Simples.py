#!/usr/bin/env python3
"""
Visualização simples da câmera PrimeSense: imagem RGB em tela cheia, com um
gráfico animado do nível do microfone (estilo onda de assistente de voz) em
miniatura num canto, e um bounding box verde ao redor de rostos detectados
(YOLOv3-face) na imagem RGB. Sem sidebar, sem outros modos — só isso.

Uso:
    python3 Camera_Simples.py [--corner tl|tr|bl|br] [--windowed]
    python3 Camera_Simples.py --no-faces          desliga a deteccao de rosto
    python3 Camera_Simples.py --face-size 128      entrada da rede menor = mais rapido, menos preciso

Sai com 'q' ou ESC.

Deteccao de rosto: YOLOv3 (Darknet, treinado no WIDER FACE por
github.com/sthanhng/yoloface) via cv2.dnn. Essa Jetson roda o OpenCV do
JetPack sem CUDA no modulo dnn, entao o YOLOv3 completo e pesado demais pra
rodar a cada frame (~5s/frame em 416x416 na CPU). Por isso a deteccao roda
num PROCESSO separado (nao thread — na pratica o cv2.dnn desse OpenCV velho
trava o main loop e ate o encerramento por sinal quando dividido so por
thread; processo isola isso de vez), em resolucao reduzida (--face-size,
padrao 160 -> ~1 deteccao/seg), e o laço principal so desenha o ultimo
bounding box encontrado sobre o frame atual — o video continua fluido, so a
caixinha "atualiza" com um pequeno atraso.
"""
import argparse
import os
import signal
import subprocess
import sys
import threading
import time
import types

import camera_log
camera_log.instalar()          # ja aqui: os avisos de import tambem saem com data/hora e nivel

import cv2
import numpy as np
from primesense import openni2

from camera_utils import (HUD_AMARELO, HUD_CIANO, HudTela, MedidorDistancia,
                          configurar_cor, configurar_depth, criar_detector_rosto,
                          desenhar_alvos_hud, localizar_openni2_redist)

from comandos import registrar_comandos
from controle import ServidorControle
from saudacao import Saudador

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# jetson.mic_vad (e, mais adiante, jetson.chat_client) vivem no repo
# LSA_robot, fora dessa pasta — ver README do Desktop sobre a divisao
# repo-vs-app-rodando. Import defensivo: sem o mic, a camera continua
# funcionando normalmente (mesmo espirito do detector de rosto ausente).
sys.path.insert(0, os.path.expanduser("~/dev/LSA_robot/src"))
from common.janela import tamanho_tela_cheia
try:
    from jetson.mic_vad import OuvinteVAD, ErroDeMic, preparar_mic
except Exception as _erro_import_mic:
    OuvinteVAD = None
    preparar_mic = None
    ErroDeMic = Exception
    print(f"AVISO: jetson.mic_vad indisponivel ({_erro_import_mic}); "
          "seguindo sem indicador de microfone.", file=sys.stderr)
try:
    from jetson.mic_mock import OuvinteMock
except Exception as _erro_import_mock:
    OuvinteMock = None
    print(f"AVISO: jetson.mic_mock indisponivel ({_erro_import_mock}); "
          "--mic-mock nao vai funcionar.", file=sys.stderr)
try:
    from jetson.transcritor import ClienteTranscricao, ErroDeTranscricao
    from jetson.conversa_client import ClienteConversa, HOST_PADRAO, PORTA_PADRAO
    from painel_texto import PainelTranscricao
except Exception as _erro_import_stt:
    ClienteTranscricao = None
    ErroDeTranscricao = Exception
    PainelTranscricao = None
    ClienteConversa, HOST_PADRAO, PORTA_PADRAO = None, "10.10.10.1", 5005
    print(f"AVISO: transcricao local indisponivel ({_erro_import_stt}); "
          "seguindo sem janela de texto.", file=sys.stderr)
try:
    from jetson.chat_client import ClienteChat, ErroDeChat
except Exception as _erro_import_chat:
    ClienteChat = None
    ErroDeChat = Exception
    print(f"AVISO: jetson.chat_client indisponivel ({_erro_import_chat}); "
          "seguindo sem chat.", file=sys.stderr)
# ─── Sensor ─────────────────────────────────────────────────────────────
def iniciar_sensor(res="auto", distancia=True):
    """So abre o stream de cor. O stream de profundidade nao e mais usado
    (a miniatura do canto agora e o grafico de som, nao a profundidade) —
    fora de nao servir mais pra nada aqui, era a parte mais pesada de CPU
    (inpaint, Canny, normalizacao por percentil a cada frame) e mais
    instavel do sensor nessa Jetson (trava/precisa de replug com
    frequencia)."""
    openni2.initialize(localizar_openni2_redist())
    dev = openni2.Device.open_any()
    color_stream = dev.create_color_stream()
    configurar_cor(color_stream, res)
    color_stream.start()
    depth_stream = None
    if distancia:
        # profundidade so pra medir a distancia dos rostos. Se falhar, o app
        # segue normalmente so com a cor.
        try:
            depth_stream = dev.create_depth_stream()
            configurar_depth(depth_stream)
            try:
                dev.set_image_registration_mode(openni2.IMAGE_REGISTRATION_DEPTH_TO_COLOR)
            except Exception as e:
                print(f"AVISO: registro depth->cor indisponivel ({e})", file=sys.stderr)
            depth_stream.start()
        except Exception as e:
            print(f"AVISO: profundidade indisponivel ({e}); sem distancia.", file=sys.stderr)
            depth_stream = None
    return dev, color_stream, depth_stream


def parar_sensor(color_stream, depth_stream=None):
    for st in (depth_stream, color_stream):
        try:
            if st is not None:
                st.stop()
        except Exception:
            pass
    try:
        openni2.unload()
    except Exception:
        pass


# ─── Leitura de frames ──────────────────────────────────────────────────
def ler_color(color_stream):
    frame = color_stream.read_frame()
    img = np.frombuffer(frame.get_buffer_as_uint8(), dtype=np.uint8)
    img = img.reshape(frame.height, frame.width, 3)
    return cv2.cvtColor(img, cv2.COLOR_RGB2BGR)


class LeitorDepth:
    """Le a profundidade numa thread propria e guarda so o mapa mais recente.
    read_frame() do OpenNI2 bloqueia sem timeout — se o stream de
    profundidade travar (visto depois de resets USB), so ESTA thread fica
    presa; o video continua e a distancia some (mapa() devolve None depois
    de ATRASO_MAX_S sem quadro novo)."""

    ATRASO_MAX_S = 1.0

    def __init__(self, depth_stream):
        self._stream = depth_stream
        self._mapa = None
        self._t = 0.0
        self._lock = threading.Lock()
        self._rodando = True
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        n, erros, t_log = 0, 0, time.time()
        while self._rodando:
            try:
                frame = self._stream.read_frame()
                mapa = np.frombuffer(frame.get_buffer_as_uint16(), dtype=np.uint16).reshape(
                    frame.height, frame.width).copy()
            except Exception as e:
                if erros == 0:
                    print(f"AVISO: erro lendo profundidade ({e!r})", file=sys.stderr, flush=True)
                erros += 1
                time.sleep(0.2)
                continue
            n += 1
            with self._lock:
                self._mapa, self._t = mapa, time.time()
            if time.time() - t_log > 5:
                validos = int((mapa > 0).sum())
                print(f"depth: {n} quadros, {erros} erros, {validos}/{mapa.size} px validos, "
                      f"centro={int(mapa[mapa.shape[0] // 2, mapa.shape[1] // 2])} mm",
                      file=sys.stderr, flush=True)
                n, erros, t_log = 0, 0, time.time()

    def mapa(self):
        with self._lock:
            if self._mapa is None or time.time() - self._t > self.ATRASO_MAX_S:
                return None
            return self._mapa

    def parar(self):
        self._rodando = False


# ─── Composição da tela ─────────────────────────────────────────────────
def _texto_com_contorno(img, texto, pos, escala, cor):
    """cv2.putText com um contorno preto por baixo, pra ficar legivel mesmo
    quando a onda de som (ou qualquer outra coisa clara) passa atras."""
    cv2.putText(img, texto, pos, cv2.FONT_HERSHEY_SIMPLEX, escala,
                (0, 0, 0), 3, cv2.LINE_AA)
    cv2.putText(img, texto, pos, cv2.FONT_HERSHEY_SIMPLEX, escala,
                cor, 1, cv2.LINE_AA)


class VisualizadorSom:
    """Forma de onda do microfone em barras espelhadas (estilo assistente de
    voz), desenhada num painel de vidro translucido: o video continua
    aparecendo atras, so escurecido (TINT). Le
    jetson.mic_vad.OuvinteVAD.amostras_recentes() (audio cru, mono, int16) e
    usa o pico de cada faixa de amostras como altura de uma barra."""

    COR_HUD = (255, 220, 0)      # ciano, mesma paleta do resto do HUD
    COR_FALA = (90, 255, 60)     # verde quando acima do limiar (falando)
    COR_OFFLINE = (60, 60, 255)  # vermelho — parou de verdade, nao e so silencio
    TINT = 0.55                  # quanto escurece o video por baixo (0 = transparente)
    BARRA_PX = 5                 # largura de cada barra (a 1080p)
    GAP_PX = 3

    def __init__(self):
        self._suave = None       # alturas anteriores, pra barras descerem suavemente

    def estado_visual(self, ouvinte):
        """(texto, cor) do indicador do cabecalho."""
        if ouvinte is None:
            return "MICROFONE", self.COR_HUD
        _, _, gravando, vivo = ouvinte.estado()
        if not vivo:
            return "MIC OFFLINE", self.COR_OFFLINE
        return ("OUVINDO", self.COR_FALA) if gravando else ("MICROFONE", self.COR_HUD)

    def desenhar(self, regiao, ouvinte, k=1.0):
        """Desenha as barras em `regiao` (area livre abaixo do cabecalho)."""
        altura, largura = regiao.shape[:2]
        meio_y = altura // 2
        barra = max(2, int(self.BARRA_PX * k))
        passo = barra + max(1, int(self.GAP_PX * k))
        n = max(1, largura // passo)
        x_ini = (largura - n * passo + (passo - barra)) // 2

        _, cor = self.estado_visual(ouvinte)
        alturas = np.zeros(n, np.float32)

        if ouvinte is not None:
            nivel, limiar, gravando, vivo = ouvinte.estado()
            amostras = ouvinte.amostras_recentes() if vivo else []
            if vivo and len(amostras) >= n:
                dados = np.frombuffer(amostras, dtype=np.int16).astype(np.float32)
                bucket = len(dados) // n
                dados = dados[-n * bucket:].reshape(n, bucket)
                pico = np.maximum(np.abs(dados.min(axis=1)), np.abs(dados.max(axis=1)))
                # teto derivado do limiar de fala: voz normal enche boa parte do painel
                alturas = np.clip(pico / max(limiar * 6, 3000), 0, 1)

        # subida imediata, descida suave — barras "respiram" em vez de piscar
        if self._suave is None or len(self._suave) != n:
            self._suave = np.zeros(n, np.float32)
        self._suave = np.maximum(alturas, self._suave * 0.82)
        meia = (self._suave * (altura * 0.5 - 2)).astype(np.int32)

        for i in range(n):
            x = x_ini + i * passo
            h = max(1, int(meia[i]))          # 1px de "ponto" mesmo em silencio
            # borda das barras mais escura -> efeito de degrade do centro pra fora
            cv2.rectangle(regiao, (x, meio_y - h), (x + barra - 1, meio_y + h), cor, -1)
        cv2.line(regiao, (0, meio_y), (largura, meio_y), cor, 1, cv2.LINE_AA)
        return regiao


def compor_hud_transparente(fundo, visualizador, ouvinte, canto="br", margem=20,
                             escala=0.24, proporcao=0.36, rotulo=None):
    """Painel de vidro com a onda do mic, no `canto` da tela: fundo do video
    escurecido, moldura fina com cantos, cabecalho com indicador de status.
    Tamanho e margens acompanham a resolucao (referencia 1080p)."""
    h, w = fundo.shape[:2]
    k = max(0.6, h / 1080.0)
    mw = int(w * escala)
    mh = int(mw * proporcao)
    mx = max(margem, int(70 * k))     # afasta dos colchetes/rodape do HUD
    my = max(margem, int(90 * k))

    if canto == "tl":
        x, y = mx, my
    elif canto == "tr":
        x, y = w - mw - mx, my
    elif canto == "bl":
        x, y = mx, h - mh - my
    else:  # "br"
        x, y = w - mw - mx, h - mh - my

    saida = fundo
    # vidro: escurece o video atras do painel
    saida[y:y + mh, x:x + mw] = cv2.convertScaleAbs(
        saida[y:y + mh, x:x + mw], alpha=1.0 - visualizador.TINT)

    texto, cor = visualizador.estado_visual(ouvinte)
    cab = int(34 * k)                 # altura do cabecalho
    pad = int(12 * k)

    # cabecalho: ponto de status (pulsa quando ouvindo) + texto
    cy = y + cab // 2
    pulso = 0.5 + 0.5 * np.sin(time.time() * 6) if texto == "OUVINDO" else 0.0
    cv2.circle(saida, (x + pad + int(6 * k), cy), int((6 + 2 * pulso) * k), cor, -1, cv2.LINE_AA)
    _texto_com_contorno(saida, texto, (x + pad + int(22 * k), cy + int(6 * k)), 0.6 * k, cor)
    cv2.line(saida, (x + pad, y + cab), (x + mw - pad, y + cab), cor, 1, cv2.LINE_AA)

    # onda na area abaixo do cabecalho
    area = saida[y + cab + 2:y + mh - 4, x + pad:x + mw - pad]
    visualizador.desenhar(area, ouvinte, k)

    # moldura fina + cantos grossos
    cv2.rectangle(saida, (x, y), (x + mw, y + mh), cor, 1, cv2.LINE_AA)
    tick = int(16 * k)
    for (cx_, cy_, dx, dy) in [(x, y, 1, 1), (x + mw, y, -1, 1),
                                (x, y + mh, 1, -1), (x + mw, y + mh, -1, -1)]:
        cv2.line(saida, (cx_, cy_), (cx_ + dx * tick, cy_), cor, max(2, int(3 * k)), cv2.LINE_AA)
        cv2.line(saida, (cx_, cy_), (cx_, cy_ + dy * tick), cor, max(2, int(3 * k)), cv2.LINE_AA)
    return saida


def _quebrar_linhas(texto, largura_max_px, fonte, escala, espessura):
    """Quebra `texto` em linhas que cabem em `largura_max_px` (usa
    cv2.getTextSize pra medir de verdade em vez de contar caracteres)."""
    palavras = texto.split()
    linhas = []
    atual = ""
    for palavra in palavras:
        candidato = (atual + " " + palavra).strip()
        (lw, _), _ = cv2.getTextSize(candidato, fonte, escala, espessura)
        if lw <= largura_max_px or not atual:
            atual = candidato
        else:
            linhas.append(atual)
            atual = palavra
    if atual:
        linhas.append(atual)
    return linhas or [""]


def desenhar_chat(fundo, mensagens, margem=20, altura_frac=0.5, largura_frac=0.32):
    """Painel de chat estilo HUD no canto superior direito, ate ~metade da
    altura da tela. Mensagens do usuario (transcritas do mic) alinhadas a
    ESQUERDA do painel, respostas do robo a DIREITA. `mensagens` e uma
    lista de (autor, texto) com autor "usuario"/"robo", mais recente por
    ultimo (mesmo formato de jetson.chat_client.ClienteChat.mensagens())."""
    if not mensagens:
        return fundo

    cor_hud = (255, 220, 0)      # ciano, mesma paleta dos outros paineis
    cor_robo = (120, 255, 120)   # verde suave, so pra diferenciar do usuario
    fonte = cv2.FONT_HERSHEY_SIMPLEX
    escala = 0.5
    espessura = 1
    altura_linha = 22
    pad = 12

    h, w = fundo.shape[:2]
    py = margem
    pw = int(w * largura_frac)
    ph = int(h * altura_frac)
    x0 = w - pw - margem
    x1 = w - margem

    saida = fundo
    regiao = saida[py:py + ph, x0:x1].copy()
    cv2.rectangle(regiao, (0, 0), (regiao.shape[1], regiao.shape[0]), (0, 0, 0), -1)
    saida[py:py + ph, x0:x1] = cv2.addWeighted(regiao, 0.45, saida[py:py + ph, x0:x1], 0.55, 0)

    cv2.rectangle(saida, (x0, py), (x1, py + ph), cor_hud, 1, cv2.LINE_AA)
    tick = 14
    for (cx, cy, dx, dy) in [(x0, py, 1, 1), (x1, py, -1, 1),
                              (x0, py + ph, 1, -1), (x1, py + ph, -1, -1)]:
        cv2.line(saida, (cx, cy), (cx + dx * tick, cy), cor_hud, 2, cv2.LINE_AA)
        cv2.line(saida, (cx, cy), (cx, cy + dy * tick), cor_hud, 2, cv2.LINE_AA)
    cv2.putText(saida, "CHAT", (x0, py - 8), fonte, 0.5, cor_hud, 1, cv2.LINE_AA)

    # monta os blocos de baixo pra cima (mais recente primeiro) ate estourar
    # a altura do painel, depois desenha em ordem cronologica normal
    largura_max_px = pw - 2 * pad
    blocos = []
    altura_usada = 0
    for autor, texto in reversed(mensagens):
        linhas = _quebrar_linhas(texto or "(vazio)", largura_max_px, fonte, escala, espessura)[:4]
        altura_bloco = len(linhas) * altura_linha + 8
        if altura_usada + altura_bloco > ph - 2 * pad:
            break
        blocos.append((autor, linhas))
        altura_usada += altura_bloco
    blocos.reverse()

    y = py + ph - pad - altura_usada
    for autor, linhas in blocos:
        cor = cor_hud if autor == "usuario" else cor_robo
        for linha in linhas:
            (lw, _), _ = cv2.getTextSize(linha, fonte, escala, espessura)
            tx = x0 + pad if autor == "usuario" else x1 - pad - lw
            cv2.putText(saida, linha, (tx, y + altura_linha - 6), fonte, escala,
                        cor, espessura, cv2.LINE_AA)
            y += altura_linha
        y += 8

    return saida


def _on_sigterm(signum, frame):
    # sem isso, um "kill" (SIGTERM) mata o processo na hora e pula o
    # 'finally' do main() — o OpenNI2/sensor da PrimeSense fica travado
    # (visto na pratica: precisou de um reset USB pra voltar a funcionar).
    # SystemExit propaga por 'finally' como qualquer excecao normal.
    raise SystemExit(0)


def _tela_cheia_wnck(titulo):
    codigo = (
        "import gi\n"
        "gi.require_version('Wnck','3.0')\n"
        "from gi.repository import Wnck, Gtk\n"
        "Wnck.set_client_type(Wnck.ClientType.PAGER)\n"
        "s=Wnck.Screen.get_default(); s.force_update()\n"
        "[w.set_fullscreen(True) for w in s.get_windows() if w.get_name()==%r]\n"
        "Gtk.main_iteration_do(False)\n" % titulo
    )
    try:
        subprocess.Popen(["/usr/bin/python3", "-c", codigo],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass


STATUS_ESPERA = "/tmp/c2gelsa-status"


def tela_espera():
    """Tela cheia "aguardando a camera": nao abre o sensor. Mostra o texto que
    o script de autostart escreve em STATUS_ESPERA (uma linha por \\n) e fica
    ate ser encerrada (o autostart mata este processo quando o app abre)."""
    from camera_utils import _colchetes, _texto
    largura, altura = tamanho_tela_cheia()
    k = max(0.6, altura / 1080.0)
    janela = "C-2GELSA espera"
    cv2.startWindowThread()
    cv2.namedWindow(janela, cv2.WINDOW_NORMAL)
    fundo = np.zeros((altura, largura, 3), np.uint8)
    fundo[:] = (28, 20, 12)
    fundo[::4] = (36, 27, 17)          # scanlines
    quadros = 0
    while True:
        t = time.time()
        img = fundo.copy()
        m = int(20 * k)
        _colchetes(img, m, m, largura - m, altura - m, int(44 * k), HUD_CIANO, max(2, int(3 * k)))

        (tw, _), _ = cv2.getTextSize("C-2GELSA", cv2.FONT_HERSHEY_DUPLEX, 3.2 * k, max(3, int(5 * k)))
        cx, cy = largura // 2, int(altura * 0.40)
        cv2.putText(img, "C-2GELSA", (cx - tw // 2, cy), cv2.FONT_HERSHEY_DUPLEX, 3.2 * k,
                    (255, 255, 255), max(3, int(5 * k)), cv2.LINE_AA)

        try:
            with open(STATUS_ESPERA) as f:
                linhas = f.read().strip().split("\n") or [""]
        except OSError:
            linhas = ["INICIANDO..."]
        y = cy + int(90 * k)
        for i, linha in enumerate(linhas):
            escala = (0.95 if i == 0 else 0.7) * k
            cor = HUD_AMARELO if i == 0 else HUD_CIANO
            (lw, _), _ = cv2.getTextSize(linha, cv2.FONT_HERSHEY_DUPLEX, escala, 2)
            _texto(img, linha, (cx - lw // 2, y), escala, cor)
            y += int(48 * k)

        # barra de progresso indeterminada (um trecho que corre de lado a lado)
        bw, bh = int(largura * 0.28), max(4, int(6 * k))
        bx, by = cx - bw // 2, y + int(30 * k)
        cv2.rectangle(img, (bx, by), (bx + bw, by + bh), (70, 55, 30), -1)
        seg = bw // 4
        pos = int(((t * 0.6) % 1.0) * (bw + seg)) - seg
        x0, x1 = bx + max(0, pos), bx + min(bw, pos + seg)
        if x1 > x0:
            cv2.rectangle(img, (x0, by), (x1, by + bh), HUD_CIANO, -1)

        cv2.imshow(janela, img)
        if quadros == 10:
            _tela_cheia_wnck(janela)
        quadros += 1
        cv2.waitKey(50)


def main():
    signal.signal(signal.SIGTERM, _on_sigterm)

    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--corner", choices=["tl", "tr", "bl", "br"], default="br",
                     help="canto onde o grafico de som aparece (padrão: br)")
    ap.add_argument("--windowed", action="store_true",
                     help="abre em janela normal em vez de tela cheia")
    ap.add_argument("--vitrine", action="store_true",
                     help="janela pequena num canto da tela (chama atencao sem ocupar "
                          "a tela toda) em vez de tela cheia — ignora --windowed")
    ap.add_argument("--vitrine-canto", choices=["tl", "tr", "bl", "br"], default="br",
                     help="canto da TELA onde a janela --vitrine aparece (padrao: br; "
                          "diferente de --corner, que e o canto do grafico DENTRO do video)")
    ap.add_argument("--no-faces", action="store_true",
                     help="desliga a deteccao de rosto (YOLO-face; roda em processo a parte)")
    ap.add_argument("--face-size", type=int, default=128,
                     help="entrada da rede YOLO em pixels, multiplo de 32 (padrao 128; menor = mais rapido)")
    ap.add_argument("--res", default="auto",
                     help="resolucao da camera de cor: auto (maior com >=15fps) ou LxA, ex. 640x480")
    ap.add_argument("--face-conf", type=float, default=0.5,
                     help="confianca minima da deteccao de rosto (padrao 0.5)")
    ap.add_argument("--espera", action="store_true",
                     help="so mostra a tela 'aguardando a camera' (usada pelo autostart)")
    ap.add_argument("--no-distancia", action="store_true",
                     help="nao le a profundidade (sem a distancia dos rostos)")
    ap.add_argument("--no-mic", action="store_true",
                     help="desliga o grafico de nivel do microfone")
    ap.add_argument("--mic-limiar", type=int, default=None,
                     help="limiar de RMS pra considerar 'falando' (ajuste fino do VAD)")
    ap.add_argument("--mic-ganho", type=int, default=2300,
                     help="ganho de captura do mic da PrimeSense, 0-4182 (padrao 2300; "
                          "o do mic_vad e 1700 e exigia falar alto; 3576 de fabrica satura)")
    ap.add_argument("--mic-silencio", type=float, default=1.0,
                     help="segundos de silencio pra fechar a frase (padrao 0.6)")
    ap.add_argument("--mic-mock", action="store_true",
                     help="grafico de onda com dados SIMULADOS (jetson.mic_mock), sem "
                          "precisar de mic de verdade — util quando o hardware esta "
                          "instavel mas ainda se quer mostrar o grafico bonito. Nunca "
                          "manda audio pro chat (so visual). Ignora --mic-limiar/--no-mic")
    ap.add_argument("--stt", choices=("local", "pc", "off"), default="local",
                     help="janela de texto com a fala: 'local' transcreve na propria Jetson "
                          "(Vosk, offline; padrao), 'pc' usa o chat com o pc.server_voz, "
                          "'off' desliga")
    ap.add_argument("--cerebro", default="%s:%d" % (HOST_PADRAO, PORTA_PADRAO), metavar="HOST:PORTA",
                     help="servidor de conversa na Jetson 1 (padrao %s:%d, pelo cabo). Cada frase "
                          "transcrita vai pra la e a resposta (texto + voz) volta; so vale com "
                          "--stt local" % (HOST_PADRAO, PORTA_PADRAO))
    ap.add_argument("--chat-off", action="store_true",
                     help="abre com a conversa por voz DESLIGADA: o microfone so transcreve e a fala "
                          "nao vai ao cerebro (religue com `camera chat on`; `type` sempre responde)")
    ap.add_argument("--no-cerebro", action="store_true",
                     help="so transcreve, sem conversar com a Jetson 1")
    ap.add_argument("--no-chat", action="store_true",
                     help="atalho pra --stt off (so fica o indicador de mic)")
    ap.add_argument("--chat-host", default="127.0.0.1",
                     help="IP do PC rodando pc.server_voz (padrao 127.0.0.1)")
    ap.add_argument("--chat-port", type=int, default=5000,
                     help="porta do pc.server_voz (padrao 5000)")
    ap.add_argument("--saudar", action="store_true",
                     help="TESTE: fala uma saudacao quando um rosto chega perto "
                          "(precisa da profundidade e da deteccao de rosto ligadas)")
    ap.add_argument("--saudar-dist-min", type=float, default=0.8, metavar="M",
                     help="distancia minima em metros pra saudar (padrao 0.8)")
    ap.add_argument("--saudar-dist-max", type=float, default=1.2, metavar="M",
                     help="distancia maxima em metros pra saudar (padrao 1.2)")
    ap.add_argument("--saudar-texto", default="Bom dia",
                     help="frase falada na saudacao (padrao 'Bom dia')")
    ap.add_argument("--saudar-intervalo", type=float, default=20.0, metavar="S",
                     help="segundos minimos entre duas saudacoes (padrao 20)")
    ap.add_argument("--dump-frames", metavar="DIR", default=None,
                     help="salva um quadro cru da camera a cada 0.4 s em DIR (pra avaliar detectores offline)")
    ap.add_argument("--profile", action="store_true",
                     help="imprime no log o tempo medio de cada etapa do pipeline")
    args = ap.parse_args()
    if args.espera:
        return tela_espera()
    print("[app] iniciado (pid %d, args: %s)" % (os.getpid(), " ".join(sys.argv[1:]) or "nenhum"),
          flush=True)

    detector = None if args.no_faces else criar_detector_rosto(args.face_size, args.face_conf)
    rosto = types.SimpleNamespace(detector=detector)   # o comando `faces` liga/desliga aqui

    # OpenNI2 abre a interface de video da PrimeSense PRIMEIRO, sem nada mais
    # mexendo no mesmo dispositivo USB ao mesmo tempo — visto na pratica
    # 2026-09-14 que abrir o mic (arecord -l + Popen) concorrente com o
    # Device.open_any() do OpenNI2 deixa a interface de audio travada
    # (arecord -l trava mesmo, precisa de replug fisico pra voltar). So
    # inicializa o mic/chat DEPOIS do sensor de video estar de pe — e com
    # uma pausa curta no meio: so sequenciar nao bastou na pratica (visto
    # 2026-09-14 em varios testes seguidos: o mic falha quase toda vez que
    # abre logo apos o video, mesmo sozinho — sem o video aberto junto —
    # funciona sempre; parece precisar de um respiro no barramento USB
    # depois que o stream de video comeca a transferir de verdade).
    if preparar_mic is not None and not args.no_mic and not args.mic_mock:
        preparar_mic()   # antes do video: ver o motivo em jetson.mic_vad.preparar_mic
    _dev, color_stream, depth_stream = iniciar_sensor(args.res, not args.no_distancia)
    medidor = MedidorDistancia() if depth_stream is not None else None
    leitor_depth = LeitorDepth(depth_stream) if depth_stream is not None else None
    time.sleep(1.5)
    visual_som = VisualizadorSom()

    def criar_ouvinte():
        kwargs = {"ganho": args.mic_ganho, "silencio_s": args.mic_silencio}
        if args.mic_limiar:
            kwargs["limiar_fala"] = args.mic_limiar
        return OuvinteVAD(**kwargs)

    ouvinte = None
    if args.mic_mock:
        if OuvinteMock is not None:
            ouvinte = OuvinteMock()
        else:
            print("AVISO: jetson.mic_mock indisponivel; seguindo sem indicador de mic.",
                  file=sys.stderr)
    elif not args.no_mic and OuvinteVAD is not None:
        try:
            ouvinte = criar_ouvinte()
        except ErroDeMic as e:
            print(f"AVISO: microfone indisponivel ({e}); seguindo sem indicador de mic.",
                  file=sys.stderr)

    if args.no_chat:
        args.stt = "off"

    transcritor = painel_texto = conversa = None
    if args.stt == "local" and not args.mic_mock:
        if ClienteTranscricao is None:
            pass  # o aviso do import ja foi impresso
        else:
            try:
                # mesmo OuvinteVAD do indicador de mic (so pode ter um arecord por vez).
                # Sem microfone o painel e a conversa continuam: da pra digitar pelo
                # terminal `camera` (comando digitar/conversar).
                if ouvinte is None:
                    print("AVISO: sem microfone; a janela de texto funciona so por "
                          "digitacao (terminal `camera`).", file=sys.stderr)
                transcritor = ClienteTranscricao(ouvinte=ouvinte, sem_mic=ouvinte is None)
                painel_texto = PainelTranscricao()
                if not args.no_cerebro and ClienteConversa is not None:
                    host, _, porta = args.cerebro.partition(":")
                    conversa = ClienteConversa(host, int(porta or PORTA_PADRAO),
                                               ao_mensagem=transcritor.adicionar)
                    transcritor.definir_conversa(conversa)
                    transcritor.conversa_ativa = not args.chat_off
            except ErroDeTranscricao as e:
                print(f"AVISO: transcricao indisponivel ({e}); seguindo sem janela de texto.",
                      file=sys.stderr)

    # O Saudador sempre existe (o terminal `camera` pode ligar/desligar ao vivo);
    # comeca ligado so com --saudar. Precisa de rostos + profundidade pra ter distancia.
    saudavel = detector is not None and medidor is not None
    if args.saudar and not saudavel:
        print("AVISO: --saudar precisa de deteccao de rosto e profundidade "
              "(sem --no-faces/--no-distancia); saudacao desligada.", file=sys.stderr)
    saudador = Saudador(args.saudar_texto, args.saudar_dist_min, args.saudar_dist_max,
                        args.saudar_intervalo, ativo=args.saudar and saudavel,
                        disponivel=saudavel)
    if transcritor is not None:
        transcritor.definir_mudo_extra(saudador.falando)
    if conversa is not None:
        saudador.definir_voz_cerebro(conversa.falar_literal)   # mesma voz das respostas

    mic = types.SimpleNamespace(ouvinte=ouvinte)   # o `reiniciar mic` troca o ouvinte aqui
    pode_reiniciar_mic = OuvinteVAD is not None and not args.no_mic and not args.mic_mock
    servidor = ServidorControle()
    registrar_comandos(servidor, args, saudador, mic, transcritor, conversa,
                       leitor_depth, rosto,
                       criar_detector=lambda: criar_detector_rosto(args.face_size, args.face_conf),
                       criar_ouvinte=criar_ouvinte if pode_reiniciar_mic else None,
                       erro_de_mic=ErroDeMic)
    try:
        servidor.iniciar()
    except OSError as e:
        print("AVISO: canal de controle indisponivel (%s); o terminal `camera` nao vai "
              "conseguir falar com o app." % e, file=sys.stderr)

    cliente_chat = None
    if args.stt == "pc" and ClienteChat is not None:
        if ouvinte is None:
            print("AVISO: sem microfone, seguindo sem chat.", file=sys.stderr)
        else:
            try:
                # reaproveita o mesmo OuvinteVAD do indicador de mic — so pode
                # ter um arecord por vez no hw:2,0.
                cliente_chat = ClienteChat(args.chat_host, args.chat_port, ouvinte=ouvinte)
            except ErroDeChat as e:
                print(f"AVISO: chat indisponivel ({e}); seguindo sem chat.", file=sys.stderr)

    if args.vitrine:
        largura, altura = 480, 270
    elif args.windowed:
        largura, altura = 960, 720
    else:
        largura, altura = tamanho_tela_cheia()

    janela = "C-2GELSA"
    # sem isso, o backend GTK do highgui as vezes cria a janela mas nunca a
    # mapeia de verdade na tela (fica invisivel, apesar do processo estar
    # rodando e desenhando normalmente) quando o app e' lancado via SSH/nohup
    # nesse Unity/compiz — visto e confirmado na pratica 2026-09-11.
    cv2.startWindowThread()
    cv2.namedWindow(janela, cv2.WINDOW_NORMAL)

    # O pedido de tela cheia só é atendido pelo window manager depois que a
    # janela já tem conteúdo (mapeada na tela) — pedir antes do primeiro
    # imshow faz o WM ignorar o hint silenciosamente.
    primeiro_frame = True
    quadros = 0
    hud = HudTela()

    # Pipeline em 2 estagios: uma thread produz os quadros (leitura da camera,
    # resize, rastreio, HUD) enquanto a principal so faz imshow/waitKey. O
    # imshow do GTK em 1080p custa ~20-40 ms; em paralelo com o resto o FPS
    # passa a ser limitado pelo estagio mais lento, nao pela soma dos dois.
    estado = {"quadro": None, "erro": None}
    novo_quadro = threading.Event()
    parar = threading.Event()

    prof = {}
    prof_show = {}
    ultimo_dump = [0.0]
    if args.dump_frames:
        os.makedirs(args.dump_frames, exist_ok=True)

    def marca(nome, t0):
        if args.profile:
            t1 = time.time()
            prof[nome] = prof.get(nome, 0.0) + (t1 - t0)
            return t1
        return t0

    def produzir():
        try:
            n_prof, t_prof = 0, time.time()
            while not parar.is_set():
                t0 = time.time()
                bruto = ler_color(color_stream)
                if args.dump_frames and time.time() - ultimo_dump[0] > 0.4:
                    ultimo_dump[0] = time.time()
                    cv2.imwrite(os.path.join(args.dump_frames, "q%05d.png" % int(ultimo_dump[0] * 10 % 100000)), bruto)
                t0 = marca("leitura", t0)
                cor = cv2.resize(bruto, (largura, altura), interpolation=cv2.INTER_LINEAR)
                t0 = marca("resize", t0)

                alvos = []
                detector = rosto.detector
                if detector:
                    # o YOLO recebe o frame da camera (reduzido la dentro); o rastreio
                    # por fluxo optico roda a cada frame. Caixas voltam em coordenadas
                    # da camera e sao escaladas pra tela.
                    detector.submit(bruto)
                    t0 = marca("rastreio", t0)
                    ex, ey = largura / bruto.shape[1], altura / bruto.shape[0]
                    caixas = detector.alvos()
                    alvos = [(i, (int(x * ex), int(y * ey), int(w * ex), int(h * ey)), idade)
                             for (i, (x, y, w, h), idade) in caixas]
                    distancias = None
                    if medidor is not None:
                        try:
                            mapa = leitor_depth.mapa()
                            if mapa is None:      # profundidade parada: sem distancia
                                distancias = {i: None for (i, _, _) in caixas}
                            else:
                                fx, fy = mapa.shape[1] / bruto.shape[1], mapa.shape[0] / bruto.shape[0]
                                distancias = medidor.medir(mapa, [
                                    (i, (x * fx, y * fy, w * fx, h * fy)) for (i, (x, y, w, h), _) in caixas])
                        except Exception as e:
                            print(f"AVISO: falha lendo profundidade ({e})", file=sys.stderr)
                    saudador.atualizar(distancias)
                    desenhar_alvos_hud(cor, alvos, distancias)
                hud.desenhar(cor, len(alvos), detector is not None)
                t0 = marca("hud", t0)

                quadro = cor
                ouvinte_atual = mic.ouvinte
                if ouvinte_atual is not None:
                    quadro = compor_hud_transparente(quadro, visual_som, ouvinte_atual, canto=args.corner)
                if cliente_chat:
                    quadro = desenhar_chat(quadro, cliente_chat.mensagens())
                if transcritor:
                    quadro = painel_texto.desenhar(
                        quadro, transcritor.mensagens(), transcritor.estado(),
                        conversa.conectado() if conversa else None)
                t0 = marca("mic/chat", t0)
                estado["quadro"] = quadro  # nao e mais alterado depois de publicado
                novo_quadro.set()
                if args.profile:
                    n_prof += 1
                    if time.time() - t_prof > 3:
                        dt = time.time() - t_prof
                        print("PROFILE produtor %.1f fps | %s | imshow %.1f ms/quadro" % (
                            n_prof / dt,
                            " ".join("%s=%.1fms" % (k, v / n_prof * 1000) for k, v in prof.items()),
                            prof_show.get("imshow", 0.0) / max(1, prof_show.get("n", 1)) * 1000),
                            flush=True)
                        prof.clear(); prof_show.clear(); n_prof, t_prof = 0, time.time()
        except Exception as e:  # repassa pra thread principal encerrar direito
            estado["erro"] = e
            novo_quadro.set()

    produtor = threading.Thread(target=produzir, daemon=True)
    produtor.start()

    try:
        while True:
            novo_quadro.wait(timeout=1.0)
            novo_quadro.clear()
            if estado["erro"] is not None:
                raise estado["erro"]
            quadro = estado["quadro"]
            if quadro is not None:
                t_show = time.time()
                cv2.imshow(janela, quadro)
                if args.profile:
                    prof_show["imshow"] = prof_show.get("imshow", 0.0) + time.time() - t_show
                    prof_show["n"] = prof_show.get("n", 0) + 1

                if primeiro_frame:
                    if args.vitrine:
                        tela_w, tela_h = tamanho_tela_cheia()
                        if args.vitrine_canto == "tl":
                            x, y = 0, 0
                        elif args.vitrine_canto == "tr":
                            x, y = tela_w - largura, 0
                        elif args.vitrine_canto == "bl":
                            x, y = 0, tela_h - altura
                        else:  # "br"
                            x, y = tela_w - largura, tela_h - altura
                        cv2.moveWindow(janela, x, y)
                    primeiro_frame = False

                # O Unity ignora o WND_PROP_FULLSCREEN do OpenCV/GTK (a janela fica
                # so maximizada, com moldura). Pede tela cheia de verdade ao WM via
                # libwnck, num processo do python do sistema (que tem o `gi`).
                if not args.windowed and not args.vitrine and quadros == 10:
                    _tela_cheia_wnck(janela)
                quadros += 1

            tecla = cv2.waitKey(1) & 0xFF
            if tecla in (ord("q"), 27):  # 'q' ou ESC
                break
    finally:
        print("[app] encerrando", flush=True)
        parar.set()
        servidor.parar()
        produtor.join(timeout=2)
        if rosto.detector:
            rosto.detector.parar()
        if cliente_chat:
            cliente_chat.parar()
        if conversa:
            conversa.parar()
        if transcritor:
            transcritor.parar()
        if mic.ouvinte:
            mic.ouvinte.parar()
        if leitor_depth:
            leitor_depth.parar()
        parar_sensor(color_stream, depth_stream)
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
