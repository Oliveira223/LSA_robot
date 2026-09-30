import sys
import tkinter as tk

import cv2
import numpy as np
from primesense import openni2
from primesense import _openni2 as c_api
import customtkinter as ctk
from PIL import Image, ImageTk

from camera_utils import (configurar_cor, configurar_depth, criar_detector_rosto,
                          desenhar_rostos, localizar_openni2_redist)

# ─── Compat: customtkinter 3.x (Python 3.6 / Jetson aarch64) ──────────
# O código foi escrito para customtkinter >= 5.2, mas o Python 3.6 do
# JetPack só suporta a linha 3.x. Este bloco adapta a API antiga:
#   - CTkFont(...)      -> tupla de fonte do Tk
#   - ctk.StringVar etc -> tkinter.*Var
#   - font=             -> text_font=  (nome do kwarg mudou)
#   - CTkOptionMenu     -> wrapper sobre tkinter.OptionMenu
if not hasattr(ctk, "CTkFont"):
    def CTkFont(family=None, size=13, weight="normal", **_):
        fam = family or "Roboto"
        return (fam, size, "bold") if weight == "bold" else (fam, size)
    ctk.CTkFont = CTkFont

    ctk.StringVar = tk.StringVar
    ctk.BooleanVar = tk.BooleanVar
    ctk.IntVar = tk.IntVar
    ctk.DoubleVar = tk.DoubleVar

    for _wname in ("CTkLabel", "CTkButton", "CTkCheckBox", "CTkEntry"):
        _wcls = getattr(ctk, _wname, None)
        if _wcls is None:
            continue
        _worig = _wcls.__init__

        def _wpatched(self, *a, _orig=_worig, **kw):
            if "font" in kw:
                kw["text_font"] = kw.pop("font")
            kw.pop("justify", None)          # não existe na API 3.x
            _orig(self, *a, **kw)

        _wcls.__init__ = _wpatched

    if not hasattr(ctk, "CTkOptionMenu"):
        class CTkOptionMenu(tk.OptionMenu):
            def __init__(self, master, values=None, variable=None,
                         command=None, width=None, **_):
                values = list(values or [])
                if variable is None:
                    variable = tk.StringVar(value=values[0] if values else "")
                self._variable = variable
                self._command = command
                super().__init__(master, variable, *values, command=self._fire)
                self.configure(bg="#1f6aa5", fg="white", activebackground="#144870",
                               activeforeground="white", relief="flat",
                               highlightthickness=0, borderwidth=0)
                self["menu"].configure(bg="#2b2b2b", fg="white",
                                       activebackground="#1f6aa5")

            def _fire(self, value):
                if self._command:
                    self._command(value)

        ctk.CTkOptionMenu = CTkOptionMenu

ctk.set_appearance_mode("Dark")
ctk.set_default_color_theme("blue")


class XtionAppGUI(ctk.CTk):
    def __init__(self):
        super().__init__()

        self.title("PrimeSense Carmine — Painel de Controle")
        self.geometry("1100x650")
        self.minsize(900, 550)
        self.protocol("WM_DELETE_WINDOW", self.ao_fechar)

        # Tela cheia (a Jetson roda sem mouse/teclado). ESC/F11 alternam
        # caso um teclado seja conectado depois.
        self._fullscreen = ("--windowed" not in sys.argv)
        self.attributes("-fullscreen", self._fullscreen)
        self.bind("<F11>", self._toggle_fullscreen)
        self.bind("<Escape>", self._toggle_fullscreen)

        self._res_cor = "auto"
        self._face_size = 128
        self._faces_ativo = "--no-faces" not in sys.argv
        for _i, _a in enumerate(sys.argv):
            if _a == "--res" and _i + 1 < len(sys.argv):
                self._res_cor = sys.argv[_i + 1]
            if _a == "--face-size" and _i + 1 < len(sys.argv):
                self._face_size = int(sys.argv[_i + 1])
        self.detector = None          # criado sob demanda (processo YOLO)

        self.dev          = None
        self.ir_stream    = None
        self.depth_stream = None
        self.color_stream = None
        self.modo_atual   = "DEPTH"
        self.loop_id      = None
        self.colormap_idx = 0
        self.enhance      = True

        # Cache de frames para os modos multi
        self._frame_depth = None
        self._frame_ir    = None
        self._frame_color = None

        # Alguns colormaps (TURBO) só existem em OpenCV >= 4.1.2; o
        # JetPack costuma trazer 4.1.1. Ignoramos os indisponíveis.
        _cmaps_desejados = [
            ("JET",     "COLORMAP_JET"),
            ("TURBO",   "COLORMAP_TURBO"),
            ("PLASMA",  "COLORMAP_PLASMA"),
            ("INFERNO", "COLORMAP_INFERNO"),
            ("HOT",     "COLORMAP_HOT"),
        ]
        self.COLORMAPS = [
            (nome, getattr(cv2, attr))
            for nome, attr in _cmaps_desejados if hasattr(cv2, attr)
        ]

        # Modos simples vs multi
        self.MODOS_SIMPLES = ("DEPTH", "IR", "COLOR")
        self.MODOS_MULTI   = ("DUAL", "TRIAL", "QUAD")

        # Modo inicial: --mode <nome> (padrão QUAD = grade 2x2 tela cheia).
        # O PS1080 da Carmine não entrega Color junto com IR, então o QUAD
        # deriva os 4 painéis do par estável Depth + IR.
        self._modo_inicial = "QUAD"
        for _i, _a in enumerate(sys.argv):
            if _a == "--mode" and _i + 1 < len(sys.argv):
                self._modo_inicial = sys.argv[_i + 1].upper()
        if self._modo_inicial not in self.MODOS_SIMPLES + self.MODOS_MULTI:
            self._modo_inicial = "QUAD"

        if not self.inicializar_sensor():
            print("Falha ao iniciar sensor")
            sys.exit(1)

        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self._build_sidebar()
        self._build_video_area()

        self._clahe  = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        self._kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
        if self._faces_ativo and self.detector is None:  # o checkbox pode ja ter criado
            self.detector = criar_detector_rosto(self._face_size)
            if self.detector is None:
                self._faces_ativo = False
                self.faces_var.set(False)

        self.modo_atual = "__init__"
        self.mudar_modo(self._modo_inicial)
        self.atualizar_video()

    # ─── Build UI ─────────────────────────────────────────────

    def _build_sidebar(self):
        self.sidebar = ctk.CTkFrame(self, width=220, corner_radius=0)
        self.sidebar.grid(row=0, column=0, sticky="nsew")
        self.sidebar.grid_rowconfigure(10, weight=1)

        ctk.CTkLabel(self.sidebar, text="◈ PrimeSense",
                     font=ctk.CTkFont(size=18, weight="bold"),
                     text_color="#00f5c4").grid(row=0, column=0, padx=20, pady=(20, 2))

        ctk.CTkLabel(self.sidebar, text="Carmine 1.09 Short Range",
                     font=ctk.CTkFont(size=10),
                     text_color="#44445a").grid(row=1, column=0, padx=20, pady=(0, 10))

        # Separador — streams simples
        ctk.CTkLabel(self.sidebar, text="SINGLE",
                     font=ctk.CTkFont(size=9, weight="bold"),
                     text_color="#44445a").grid(row=2, column=0, padx=20, pady=(6, 2), sticky="w")

        self.btn_depth = ctk.CTkButton(self.sidebar, text="⬤  Profundidade",
                                        command=lambda: self.mudar_modo("DEPTH"))
        self.btn_depth.grid(row=3, column=0, padx=20, pady=3)

        self.btn_ir = ctk.CTkButton(self.sidebar, text="⬤  Infravermelho",
                                     command=lambda: self.mudar_modo("IR"))
        self.btn_ir.grid(row=4, column=0, padx=20, pady=3)

        self.btn_color = ctk.CTkButton(self.sidebar, text="⬤  Cor (RGB)",
                                        command=lambda: self.mudar_modo("COLOR"))
        self.btn_color.grid(row=5, column=0, padx=20, pady=3)

        # Separador — multi
        ctk.CTkLabel(self.sidebar, text="MULTI",
                     font=ctk.CTkFont(size=9, weight="bold"),
                     text_color="#44445a").grid(row=6, column=0, padx=20, pady=(10, 2), sticky="w")

        self.btn_dual = ctk.CTkButton(self.sidebar, text="⊟  Dual  (Depth + IR)",
                                       command=lambda: self.mudar_modo("DUAL"))
        self.btn_dual.grid(row=7, column=0, padx=20, pady=3)

        self.btn_quad = ctk.CTkButton(self.sidebar, text="◫  Quad  (2×2 tela cheia)",
                                       command=lambda: self.mudar_modo("QUAD"))
        self.btn_quad.grid(row=8, column=0, padx=20, pady=3)

        self.btn_trial = ctk.CTkButton(self.sidebar, text="⊞  Trial  (todos)",
                                        command=lambda: self.mudar_modo("TRIAL"))
        self.btn_trial.grid(row=9, column=0, padx=20, pady=3)

        # Colormap
        self.lbl_cmap = ctk.CTkLabel(self.sidebar, text="Colormap depth:",
                                      font=ctk.CTkFont(size=11), text_color="#888")
        self.cmap_var = ctk.StringVar(value="JET")
        self.cmap_menu = ctk.CTkOptionMenu(
            self.sidebar,
            values=[c[0] for c in self.COLORMAPS],
            variable=self.cmap_var,
            command=self._on_cmap_change,
            width=180
        )
        self.lbl_cmap.grid(row=11, column=0, padx=20, pady=(10, 2))
        self.cmap_menu.grid(row=12, column=0, padx=20, pady=(0, 4))

        # Enhance toggle
        self.enhance_var = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(self.sidebar, text="Melhorar qualidade",
                        variable=self.enhance_var,
                        command=lambda: setattr(self, 'enhance', self.enhance_var.get()),
                        font=ctk.CTkFont(size=11)).grid(row=13, column=0, padx=20, pady=6)

        self.faces_var = ctk.BooleanVar(value=self._faces_ativo)
        ctk.CTkCheckBox(self.sidebar, text="Detectar rostos (YOLO)",
                        variable=self.faces_var, command=self._on_faces_toggle,
                        font=ctk.CTkFont(size=11)).grid(row=14, column=0, padx=20, pady=6)

        # Status
        try:
            info = self.dev.get_device_info()
            nome = info.name.decode() if isinstance(info.name, bytes) else str(info.name)
        except:
            nome = "Dispositivo OK"

        ctk.CTkLabel(self.sidebar, text=f"● Online\n{nome}",
                     font=ctk.CTkFont(size=10),
                     text_color="#00f5c4", justify="center").grid(row=15, column=0, padx=20, pady=8)

        ctk.CTkButton(self.sidebar, text="✕  Sair",
                      fg_color="#7a1f1f", hover_color="#a03030",
                      command=self.ao_fechar).grid(row=16, column=0, padx=20, pady=(4, 20))

    def _build_video_area(self):
        self.video_container = ctk.CTkFrame(self, corner_radius=10)
        self.video_container.grid(row=0, column=1, padx=16, pady=16, sticky="nsew")
        self.video_container.grid_rowconfigure(0, weight=1)
        self.video_container.grid_columnconfigure(0, weight=1)

        self.lbl_video = ctk.CTkLabel(self.video_container,
                                       text="Inicializando câmera...",
                                       font=ctk.CTkFont(size=14))
        self.lbl_video.grid(row=0, column=0, sticky="nsew")

        self.lbl_info = ctk.CTkLabel(self.video_container,
                                      text="OpenNI2 · PrimeSense",
                                      font=ctk.CTkFont(size=10),
                                      text_color="#44445a")
        self.lbl_info.grid(row=1, column=0, pady=(0, 6))

    # ─── Sensor ───────────────────────────────────────────────

    def inicializar_sensor(self):
        try:
            redist = localizar_openni2_redist()
            print("OpenNI2 redist dir:", redist or "(padrão do sistema)")
            openni2.initialize(redist)
            self.dev          = openni2.Device.open_any()
            self.depth_stream = self.dev.create_depth_stream()
            self.ir_stream    = self.dev.create_ir_stream()
            self.color_stream = self.dev.create_color_stream()
            configurar_cor(self.color_stream, self._res_cor)
            configurar_depth(self.depth_stream)
            return True
        except Exception as e:
            print("Erro ao inicializar sensor:", e)
            return False

    # ─── Controles ────────────────────────────────────────────

    def _on_cmap_change(self, value):
        for i, (name, _) in enumerate(self.COLORMAPS):
            if name == value:
                self.colormap_idx = i

    def _on_faces_toggle(self):
        self._faces_ativo = self.faces_var.get()
        if self._faces_ativo and self.detector is None:
            self.detector = criar_detector_rosto(self._face_size)
            if self.detector is None:  # sem modelo: desliga a caixa
                self._faces_ativo = False
                self.faces_var.set(False)

    def _parar_todos(self):
        for s in [self.ir_stream, self.depth_stream, self.color_stream]:
            try:
                s.stop()
            except:
                pass

    def atualizar_botoes(self):
        ON, OFF = "#1f538d", "#2b2b2b"
        m = self.modo_atual
        self.btn_depth.configure(fg_color=ON if m == "DEPTH" else OFF)
        self.btn_ir.configure(   fg_color=ON if m == "IR"    else OFF)
        self.btn_color.configure(fg_color=ON if m == "COLOR" else OFF)
        self.btn_dual.configure( fg_color=ON if m == "DUAL"  else OFF)
        self.btn_quad.configure( fg_color=ON if m == "QUAD"  else OFF)
        self.btn_trial.configure(fg_color=ON if m == "TRIAL" else OFF)

    def mudar_modo(self, novo_modo):
        if novo_modo == self.modo_atual:
            return
        self._parar_todos()
        try:
            if novo_modo == "DEPTH":
                self.depth_stream.start()
            elif novo_modo == "IR":
                self.ir_stream.start()
            elif novo_modo == "COLOR":
                self.color_stream.start()
            elif novo_modo == "DUAL":
                # Depth + IR (sem color — evita conflito)
                self.depth_stream.start()
                self.ir_stream.start()
            elif novo_modo == "QUAD":
                # Grade 2x2 derivada de Depth + Color.
                # Nesta Carmine, ligar o stream de IR zera a profundidade
                # (IR e depth disputam o mesmo sensor), então o QUAD usa
                # o par estável Depth + Color.
                self.depth_stream.start()
                self.color_stream.start()
            elif novo_modo == "TRIAL":
                # Depth + IR + Color
                self.depth_stream.start()
                self.ir_stream.start()
                self.color_stream.start()

            self.modo_atual = novo_modo
            self.atualizar_botoes()
        except Exception as e:
            print("Erro ao trocar modo:", e)

    # ─── Processamento ────────────────────────────────────────

    def melhorar(self, frame_bgr):
        """CLAHE + unsharp mask. Roda no tamanho de EXIBICAO (nao no da
        camera) e sem filtro bilateral — o bilateral era o maior custo."""
        if not self.enhance:
            return frame_bgr
        lab  = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2LAB)
        l, a, b = cv2.split(lab)
        f    = cv2.cvtColor(cv2.merge([self._clahe.apply(l), a, b]), cv2.COLOR_LAB2BGR)
        blur = cv2.GaussianBlur(f, (0, 0), 1.2)
        return cv2.addWeighted(f, 1.35, blur, -0.35, 0)

    def _depth8(self):
        """Le 1 frame de profundidade -> imagem 8 bits, sem inpaint (TELEA
        era o maior custo). Buracos (0) sao preenchidos por fechamento
        morfologico, que e ordens de grandeza mais barato."""
        frame = self.depth_stream.read_frame()
        img   = np.frombuffer(frame.get_buffer_as_uint16(),
                              dtype=np.uint16).reshape(frame.height, frame.width)
        validos = img[img > 0]
        topo = int(validos.max()) if validos.size else 1
        base = int(validos.min()) if validos.size else 0
        img8 = cv2.convertScaleAbs(img, alpha=255.0 / max(1, topo - base),
                                   beta=-255.0 * base / max(1, topo - base))
        img8[img == 0] = 0
        if self.enhance:
            img8 = cv2.morphologyEx(img8, cv2.MORPH_CLOSE, self._kernel)
            img8 = cv2.GaussianBlur(img8, (5, 5), 0)
        return img8

    def ler_depth(self):
        return cv2.applyColorMap(self._depth8(), self.COLORMAPS[self.colormap_idx][1])

    def ler_depth_dual(self):
        """(colormap_bgr, cinza_bgr) a partir da MESMA leitura — modo QUAD."""
        img8 = self._depth8()
        cm   = cv2.applyColorMap(img8, self.COLORMAPS[self.colormap_idx][1])
        return cm, cv2.cvtColor(img8, cv2.COLOR_GRAY2BGR)

    def ler_ir(self, alvo=None):
        frame = self.ir_stream.read_frame()
        img   = np.frombuffer(frame.get_buffer_as_uint16(),
                              dtype=np.uint16).reshape(frame.height, frame.width)
        img8  = cv2.convertScaleAbs(img, alpha=255.0 / max(1, int(img.max())))
        bgr   = cv2.cvtColor(img8, cv2.COLOR_GRAY2BGR)
        if alvo:
            bgr = cv2.resize(bgr, alvo, interpolation=cv2.INTER_LINEAR)
        return self.melhorar(bgr)

    def ler_color(self, alvo=None):
        """Frame de cor. `alvo`=(w, h) reduz ANTES do realce e do desenho
        (a camera pode estar em 1280x960; nao faz sentido processar isso
        tudo pra mostrar num painel menor). O YOLO recebe o frame cru."""
        frame = self.color_stream.read_frame()
        img   = np.frombuffer(frame.get_buffer_as_uint8(),
                              dtype=np.uint8).reshape(frame.height, frame.width, 3)
        bgr   = cv2.cvtColor(img, cv2.COLOR_RGB2BGR)
        caixas = []
        if self._faces_ativo and self.detector is not None:
            self.detector.submit(bgr)
            caixas = self.detector.boxes()
        if alvo and alvo[0] > 50 and alvo[1] > 50:
            ex, ey = alvo[0] / bgr.shape[1], alvo[1] / bgr.shape[0]
            bgr = cv2.resize(bgr, alvo, interpolation=cv2.INTER_LINEAR)
            caixas = [(int(x * ex), int(y * ey), int(w * ex), int(h * ey)) for (x, y, w, h) in caixas]
        bgr = self.melhorar(bgr)
        desenhar_rostos(bgr, caixas)
        return bgr

    def adicionar_label(self, img, texto, cor=(255, 255, 255)):
        """Escreve um label no canto superior esquerdo do frame."""
        overlay = img.copy()
        cv2.rectangle(overlay, (0, 0), (len(texto) * 9 + 10, 24), (0, 0, 0), -1)
        cv2.addWeighted(overlay, 0.5, img, 0.5, 0, img)
        cv2.putText(img, texto, (6, 17),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, cor, 1, cv2.LINE_AA)
        return img

    def montar_grid(self, frames_labels):
        """
        Monta grade de frames com labels.
        frames_labels: lista de (frame_bgr, 'LABEL', cor)
        2 frames → lado a lado
        3 frames → 2 em cima, 1 centralizado embaixo
        """
        w = self.video_container.winfo_width() - 20
        h = self.video_container.winfo_height() - 40

        n = len(frames_labels)

        if n == 2:
            fw, fh = w // 2, h
            resized = []
            for frame, label, cor in frames_labels:
                f = cv2.resize(frame, (fw - 4, fh - 4), interpolation=cv2.INTER_LINEAR)
                f = self.adicionar_label(f, label, cor)
                resized.append(f)
            linha = np.hstack(resized)
            # Padding para bater exato
            if linha.shape[1] < w:
                pad = np.zeros((linha.shape[0], w - linha.shape[1], 3), dtype=np.uint8)
                linha = np.hstack([linha, pad])
            resultado = linha

        else:  # 3 frames — trial
            fw_top = w // 2
            fh_top = h * 2 // 3
            fw_bot = w // 2
            fh_bot = h - fh_top

            f0 = cv2.resize(frames_labels[0][0], (fw_top - 4, fh_top - 4), interpolation=cv2.INTER_LINEAR)
            f1 = cv2.resize(frames_labels[1][0], (fw_top - 4, fh_top - 4), interpolation=cv2.INTER_LINEAR)
            f2 = cv2.resize(frames_labels[2][0], (fw_bot - 4, fh_bot - 4), interpolation=cv2.INTER_LINEAR)

            f0 = self.adicionar_label(f0, frames_labels[0][1], frames_labels[0][2])
            f1 = self.adicionar_label(f1, frames_labels[1][1], frames_labels[1][2])
            f2 = self.adicionar_label(f2, frames_labels[2][1], frames_labels[2][2])

            topo  = np.hstack([f0, np.zeros((fh_top - 4, 4, 3), dtype=np.uint8), f1])
            # Centraliza f2
            pad_l = (topo.shape[1] - f2.shape[1]) // 2
            pad_r = topo.shape[1] - f2.shape[1] - pad_l
            base  = np.hstack([
                np.zeros((f2.shape[0], pad_l, 3), dtype=np.uint8),
                f2,
                np.zeros((f2.shape[0], pad_r, 3), dtype=np.uint8)
            ])
            div   = np.zeros((4, topo.shape[1], 3), dtype=np.uint8)
            resultado = np.vstack([topo, div, base])

        return resultado

    def montar_quad(self, cels):
        """4 frames numa grade 2×2 que ocupa toda a área de vídeo.
        cels: lista de exatamente 4 tuplas (frame_bgr, 'LABEL', cor)."""
        w = self.video_container.winfo_width() - 20
        h = self.video_container.winfo_height() - 40
        if w < 200 or h < 200:
            w, h = 1280, 720
        cw, ch = w // 2, h // 2

        r = []
        for frame, label, cor in cels:
            f = cv2.resize(frame, (cw - 4, ch - 4), interpolation=cv2.INTER_LINEAR)
            r.append(self.adicionar_label(f, label, cor))

        vgap = np.zeros((ch - 4, 4, 3), dtype=np.uint8)
        topo = np.hstack([r[0], vgap, r[1]])
        base = np.hstack([r[2], vgap, r[3]])
        hgap = np.zeros((4, topo.shape[1], 3), dtype=np.uint8)
        return np.vstack([topo, hgap, base])

    # ─── Loop de vídeo ────────────────────────────────────────

    def atualizar_video(self):
        try:
            modo = self.modo_atual
            w_cont = self.video_container.winfo_width() - 20
            h_cont = self.video_container.winfo_height() - 40

            if modo == "DEPTH":
                frame_bgr = self.ler_depth()
                frame_bgr = cv2.resize(frame_bgr, (w_cont, h_cont), interpolation=cv2.INTER_LINEAR) if w_cont > 100 else frame_bgr
                info = "Profundidade"

            elif modo == "IR":
                frame_bgr = self.ler_ir((w_cont, h_cont) if w_cont > 100 else None)
                frame_bgr = cv2.resize(frame_bgr, (w_cont, h_cont), interpolation=cv2.INTER_LINEAR) if w_cont > 100 else frame_bgr
                info = "Infravermelho"

            elif modo == "COLOR":
                frame_bgr = self.ler_color((w_cont, h_cont) if w_cont > 100 else None)
                frame_bgr = cv2.resize(frame_bgr, (w_cont, h_cont), interpolation=cv2.INTER_LINEAR) if w_cont > 100 else frame_bgr
                info = "Cor RGB"

            elif modo == "DUAL":
                fd = self.ler_depth()
                fi = self.ler_ir((w_cont // 2, h_cont))
                frame_bgr = self.montar_grid([
                    (fd, "DEPTH", (0, 200, 255)),
                    (fi, "IR",    (200, 200, 200)),
                ])
                info = "Dual: Depth + IR"

            elif modo == "TRIAL":
                fd = self.ler_depth()
                fi = self.ler_ir((w_cont // 2, h_cont * 2 // 3))
                fc = self.ler_color((w_cont // 2, h_cont // 3))
                frame_bgr = self.montar_grid([
                    (fd, "DEPTH", (0, 200, 255)),
                    (fi, "IR",    (200, 200, 200)),
                    (fc, "COLOR", (100, 255, 150)),
                ])
                info = "Trial: Depth + IR + Color"

            elif modo == "QUAD":
                dcm, dgy = self.ler_depth_dual()
                fc  = self.ler_color((w_cont // 2, h_cont // 2))
                cr  = cv2.resize(fc, (dcm.shape[1], dcm.shape[0]))
                ov  = cv2.addWeighted(cr, 0.6, dcm, 0.4, 0)
                frame_bgr = self.montar_quad([
                    (dcm, "DEPTH (colormap)", (0, 200, 255)),
                    (dgy, "DEPTH (cinza)",    (210, 210, 210)),
                    (fc,  "COR (RGB)",        (120, 255, 150)),
                    (ov,  "RGB + DEPTH",      (255, 180, 120)),
                ])
                info = "Quad: Depth x2 + RGB + Overlay"

            else:
                self.loop_id = self.after(16, self.atualizar_video)
                return

            frame_rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
            img_tk    = ImageTk.PhotoImage(Image.fromarray(frame_rgb))
            self.lbl_video.configure(image=img_tk, text="")
            self.lbl_video.image = img_tk
            self.lbl_info.configure(
                text=f"{info} · enhance={'ON' if self.enhance else 'OFF'} · OpenNI2"
            )

        except Exception as e:
            print("Erro no vídeo:", e)

        # modos com 3-4 painéis são mais pesados: ~30 FPS em vez de ~60
        _delay = 33
        self.loop_id = self.after(_delay, self.atualizar_video)

    # ─── Cleanup ──────────────────────────────────────────────

    def _toggle_fullscreen(self, event=None):
        self._fullscreen = not self._fullscreen
        self.attributes("-fullscreen", self._fullscreen)

    def ao_fechar(self):
        if self.loop_id:
            self.after_cancel(self.loop_id)
        self._parar_todos()
        if self.detector is not None:
            self.detector.parar()
        try:
            openni2.unload()
        except:
            pass
        self.destroy()


if __name__ == "__main__":
    app = XtionAppGUI()
    # kill/SIGTERM fecha direito (libera o sensor); sem isso o PS1080 trava
    import signal
    signal.signal(signal.SIGTERM, lambda *_: app.ao_fechar())
    app.mainloop()