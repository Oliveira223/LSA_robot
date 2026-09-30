"""
Saudacao por proximidade: quando um rosto fica a uma distancia entre `dist_min`
e `dist_max` (padrao 0.8 a 1.2 m), o robo fala uma frase fixa (padrao "Bom dia")
na caixa HDMI.

Comeca ligada com --saudar no Camera_Simples.py e pode ser ligada/desligada e
reajustada ao vivo pelo terminal `camera` (comando `saudar`), sem reiniciar o
app. Evita falar repetido:
  - o rosto precisa aparecer na faixa por `quadros_min` quadros seguidos
    (filtra deteccao espuria / distancia ruidosa);
  - depois de falar, espera `intervalo_s` antes de saudar de novo;
  - nao fala por cima de si mesmo.

Motor de voz (o primeiro que funcionar):
  0. a voz do robo: a Jetson 1 sintetiza com o Piper (a mesma voz das respostas
     do cerebro) e esta Jetson toca — ver ClienteConversa.falar_literal. Exige o
     servidor_conversa.py novo na Jetson 1; se ela estiver fora do ar ou com o
     servidor antigo, cai pra um dos abaixo e avisa;
  1. jetson/bin/say  — Piper, se ~/piper/piper estiver instalado nesta Jetson;
  2. audio/bom_dia.wav (ao lado deste arquivo), tocado com paplay — gere na
     Jetson 1 com `say -o bom_dia.wav "Bom dia"` e copie pra ca. So vale pra
     frase padrao "Bom dia";
  3. spd-say -l pt-BR (espeak, voz robotica) — so pra nao ficar mudo.

`falando()` fica True durante a fala (+ cauda) pra o transcritor nao ouvir a
propria voz do robo. `falar(texto)` serve tambem pro comando `falar` do terminal.

Compatibilidade: Python 3.6 da Jetson.
"""
import os
import subprocess
import threading
import time

SAY_BIN = os.path.expanduser("~/dev/LSA_robot/src/jetson/bin/say")
PIPER_BIN = os.path.expanduser("~/piper/piper")
WAV_PADRAO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "audio", "bom_dia.wav")
TEXTO_PADRAO = "Bom dia"
SINK = "alsa_output.platform-3510000.hda.hdmi-stereo-extra1"   # caixa HDMI (igual ao conversa_client)
CAUDA_S = 0.7


class Saudador:
    def __init__(self, texto=TEXTO_PADRAO, dist_min=0.8, dist_max=1.2, intervalo_s=20.0,
                 quadros_min=5, wav=WAV_PADRAO, ativo=False, disponivel=True):
        self.texto = texto
        self.dist_min = dist_min
        self.dist_max = dist_max
        self.intervalo_s = intervalo_s
        self.quadros_min = quadros_min
        self.wav = wav
        self.ativo = ativo
        # False quando o app roda sem deteccao de rosto ou sem profundidade:
        # nao ha distancia pra comparar, entao a saudacao nao pode ligar.
        self.disponivel = disponivel
        self._seguidos = 0
        self._proxima_ate = 0.0       # so saudar de novo depois disso
        self._mudo_ate = 0.0
        self._ocupado = False
        self._lock = threading.Lock()
        self._voz_cerebro = None      # funcao(texto) -> (ok, msg), ver definir_voz_cerebro
        self._cerebro_ruim_ate = 0.0  # depois de uma falha, nao insiste por um tempo

    def definir_voz_cerebro(self, funcao):
        """funcao(texto) -> (ok, mensagem): fala com a voz do robo via Jetson 1
        (ClienteConversa.falar_literal). Sem ela, usa so os motores locais."""
        self._voz_cerebro = funcao

    def falando(self):
        return self._ocupado or time.time() < self._mudo_ate

    def ligar(self, ligado):
        self.ativo = bool(ligado)
        self._seguidos = 0
        self._proxima_ate = 0.0

    def atualizar(self, distancias):
        """Chamar a cada quadro com {id_do_alvo: metros ou None}."""
        if not self.ativo:
            return
        na_faixa = any(d is not None and self.dist_min <= d <= self.dist_max
                       for d in (distancias or {}).values())
        self._seguidos = self._seguidos + 1 if na_faixa else 0
        if self._seguidos < self.quadros_min or time.time() < self._proxima_ate:
            return
        self._proxima_ate = time.time() + self.intervalo_s
        threading.Thread(target=self._saudar, daemon=True).start()

    def _saudar(self):
        print("[saudacao] rosto na faixa %.1f-%.1f m" % (self.dist_min, self.dist_max), flush=True)
        self.falar(self.texto)

    def _comando(self, texto):
        """(nome, argv, env) do motor de voz disponivel."""
        env = dict(os.environ, PULSE_SINK=SINK)
        if os.path.exists(PIPER_BIN) and os.path.exists(SAY_BIN):
            return "say/piper", [SAY_BIN, "-1", texto], env
        if texto == TEXTO_PADRAO and self.wav and os.path.exists(self.wav):
            return "wav", ["paplay", "--device=" + SINK, self.wav], env
        return "spd-say", ["spd-say", "-w", "-l", "pt-BR", texto], env

    def falar(self, texto):
        """Fala `texto` na caixa (bloqueia ate terminar). Devolve uma linha
        dizendo o que aconteceu. Nao fala por cima de si mesmo."""
        with self._lock:
            if self._ocupado:
                return "ja estou falando; tente de novo em instantes"
            self._ocupado = True
        aviso = ""
        try:
            if self._voz_cerebro is not None and time.time() >= self._cerebro_ruim_ate:
                ok, msg = self._voz_cerebro(texto)
                if ok:
                    print("[saudacao] falando %r (%s)" % (texto, msg), flush=True)
                    return "falei %r (%s)" % (texto, msg)
                if "nao respondeu" in msg:          # so o timeout e lento: evita repeti-lo a cada fala
                    self._cerebro_ruim_ate = time.time() + 30.0
                aviso = " [ATENCAO: fora da voz do robo: %s]" % msg
                print("[saudacao] fora da voz do robo: %s" % msg, flush=True)
            nome, argv, env = self._comando(texto)
            print("[saudacao] falando %r (%s)" % (texto, nome), flush=True)
            r = subprocess.run(argv, env=env, timeout=30, stdin=subprocess.DEVNULL,
                               start_new_session=True,
                               stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            if (r.returncode != 0 and nome == "spd-say"
                    and b"Speech Dispatcher" in r.stderr):
                # o daemon nao estava de pe e o autospawn do spd-say falha sem
                # terminal (visto no app aberto pelo launcher): sobe na mao e repete
                subprocess.run(["speech-dispatcher", "--spawn"], env=env, timeout=15,
                               stdin=subprocess.DEVNULL, start_new_session=True,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                time.sleep(1.0)
                r = subprocess.run(argv, env=env, timeout=30, stdin=subprocess.DEVNULL,
                                   start_new_session=True,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            if r.returncode != 0:
                erro = r.stderr.decode(errors="replace").strip()[:300]
                print("[saudacao] %s falhou (rc=%d): %s" % (nome, r.returncode, erro), flush=True)
                return "%s falhou (rc=%d): %s%s" % (nome, r.returncode, erro, aviso)
            return "falei %r (%s)%s" % (texto, nome, aviso)
        except (OSError, subprocess.SubprocessError) as e:
            print("[saudacao] falha ao falar: %s" % e, flush=True)
            return "falha ao falar: %s" % e
        finally:
            self._mudo_ate = time.time() + CAUDA_S
            self._ocupado = False
