"""
Saudacao de teste: quando um rosto fica a uma distancia entre `dist_min` e
`dist_max` (padrao 0.8 a 1.2 m), o robo fala uma frase fixa (padrao "Bom dia")
na caixa HDMI.

Ligada por --saudar no Camera_Simples.py. Evita falar repetido:
  - o rosto precisa aparecer na faixa por `quadros_min` quadros seguidos
    (filtra deteccao espuria / distancia ruidosa);
  - depois de falar, espera `intervalo_s` antes de saudar de novo;
  - nao fala por cima de si mesmo.

Motor de voz (o primeiro que existir):
  1. jetson/bin/say  — Piper, se ~/piper/piper estiver instalado nesta Jetson;
  2. audio/bom_dia.wav (ao lado deste arquivo), tocado com paplay — gere na
     Jetson 1 com `say -o bom_dia.wav "Bom dia"` e copie pra ca;
  3. spd-say -l pt-BR (espeak, voz robotica) — so pra nao ficar mudo.

`falando()` fica True durante a fala (+ cauda) pra o transcritor nao ouvir a
propria voz do robo.

Compatibilidade: Python 3.6 da Jetson.
"""
import os
import subprocess
import threading
import time

SAY_BIN = os.path.expanduser("~/dev/LSA_robot/src/jetson/bin/say")
PIPER_BIN = os.path.expanduser("~/piper/piper")
WAV_PADRAO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "audio", "bom_dia.wav")
SINK = "alsa_output.platform-3510000.hda.hdmi-stereo-extra1"   # caixa HDMI (igual ao conversa_client)
CAUDA_S = 0.7


class Saudador:
    def __init__(self, texto="Bom dia", dist_min=0.8, dist_max=1.2, intervalo_s=20.0,
                 quadros_min=5, wav=WAV_PADRAO):
        self.texto = texto
        self.dist_min = dist_min
        self.dist_max = dist_max
        self.intervalo_s = intervalo_s
        self.quadros_min = quadros_min
        self.wav = wav
        self._seguidos = 0
        self._proxima_ate = 0.0       # so saudar de novo depois disso
        self._mudo_ate = 0.0
        self._ocupado = False
        self._lock = threading.Lock()

    def falando(self):
        return self._ocupado or time.time() < self._mudo_ate

    def atualizar(self, distancias):
        """Chamar a cada quadro com {id_do_alvo: metros ou None}."""
        na_faixa = any(d is not None and self.dist_min <= d <= self.dist_max
                       for d in (distancias or {}).values())
        self._seguidos = self._seguidos + 1 if na_faixa else 0
        if self._seguidos < self.quadros_min or time.time() < self._proxima_ate:
            return
        with self._lock:
            if self._ocupado:
                return
            self._ocupado = True
        self._proxima_ate = time.time() + self.intervalo_s
        threading.Thread(target=self._falar, daemon=True).start()

    def _comando(self):
        """(nome, argv, env) do motor de voz disponivel, ou None."""
        env = dict(os.environ, PULSE_SINK=SINK)
        if os.path.exists(PIPER_BIN) and os.path.exists(SAY_BIN):
            return "say/piper", [SAY_BIN, "-1", self.texto], env
        if self.wav and os.path.exists(self.wav):
            return "wav", ["paplay", "--device=" + SINK, self.wav], env
        return "spd-say", ["spd-say", "-w", "-l", "pt-BR", self.texto], env

    def _falar(self):
        try:
            nome, argv, env = self._comando()
            print("[saudacao] rosto na faixa %.1f-%.1f m: falando %r (%s)"
                  % (self.dist_min, self.dist_max, self.texto, nome), flush=True)
            r = subprocess.run(argv, env=env, timeout=30,
                               stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            if r.returncode != 0:
                print("[saudacao] %s falhou (rc=%d): %s"
                      % (nome, r.returncode, r.stderr.decode(errors="replace").strip()[:300]),
                      flush=True)
        except (OSError, subprocess.SubprocessError) as e:
            print("[saudacao] falha ao falar: %s" % e, flush=True)
        finally:
            self._mudo_ate = time.time() + CAUDA_S
            self._ocupado = False
