"""
Logs organizados do app da camera, sem reescrever cada print do projeto.

`instalar()` envolve sys.stdout/sys.stderr: cada linha que o app imprime ganha data e
hora, um NIVEL e uma TAG e sai no formato

    2026-09-30 15:42:10 INFO  [saudacao] falando 'Bom dia' (voz do robo ...)

Dois destinos:
  1. o destino de sempre (terminal ou /tmp/camera-simples.log do launcher): TUDO, com
     nivel DEBUG pro ruido (quadros de profundidade, falas descartadas, audio recebido...);
     em terminal de verdade as linhas saem coloridas por nivel/tag;
  2. um arquivo por dia, LOGS_DIR/AAAA-MM-DD.log, so com o que IMPORTA: avisos, erros,
     eventos (conexoes, frases reconhecidas, saudacao, comandos do terminal `camera`,
     inicio/fim do app). Linhas iguais e seguidas viram "(repetido N vezes)". Os arquivos
     com mais de RETENCAO_DIAS dias sao apagados ao iniciar.

Niveis: DEBUG (ruido), INFO, WARN, ERROR. WARN e ERROR sao sempre importantes.

Variaveis de ambiente: CAMERA_LOG_DIR muda a pasta dos arquivos diarios.

Compatibilidade: Python 3.6 da Jetson. So biblioteca padrao.
"""
import atexit
import datetime
import os
import re
import sys
import threading
import time

LOGS_DIR = os.environ.get("CAMERA_LOG_DIR") or os.path.expanduser("~/dev/LSA_robot/logs/camera")
RETENCAO_DIAS = 30
REPETIDA_JANELA_S = 60.0          # linhas iguais dentro dessa janela viram "(repetido N vezes)"

# ── formato e cor ───────────────────────────────────────────────────────
FORMATO = re.compile(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d) (DEBUG|INFO |WARN |ERROR) \[([A-Za-z0-9_]+)\] (.*)$")
_COR_NIVEL = {"ERROR": "\033[1;31m", "WARN ": "\033[33m", "DEBUG": "\033[2m"}
_COR_TAG = {"saudacao": "\033[35m", "conversa": "\033[36m", "controle": "\033[36m",
            "transcritor": "\033[32m", "app": "\033[1m"}
_FIM = "\033[0m"


def colorir(linha):
    """Devolve `linha` (ja no FORMATO) com cores ANSI: nivel manda (erro vermelho, aviso
    amarelo, ruido apagado); em INFO a cor vem da tag. Linha fora do formato: apagada."""
    m = FORMATO.match(linha)
    if not m:
        return "\033[2m" + linha + _FIM
    cor = _COR_NIVEL.get(m.group(2)) or _COR_TAG.get(m.group(3))
    return (cor + linha + _FIM) if cor else linha


# ── classificacao ───────────────────────────────────────────────────────
_TAG = re.compile(r"^\[([A-Za-z0-9_]+)\]\s*(.*)$")
_SENSOR = re.compile(r"^(Cor|Depth|Rosto):\s*(.*)$")
_DEPTH = re.compile(r"^depth: (\d+) quadros, (\d+) erros")
_DUMP_ARECORD = re.compile(r"^(ACCESS|FORMAT|SUBFORMAT|SAMPLE_BITS|FRAME_BITS|CHANNELS|RATE|PERIOD_TIME|"
                           r"PERIOD_SIZE|PERIOD_BYTES|PERIODS|BUFFER_TIME|BUFFER_SIZE|BUFFER_BYTES|TICK_TIME)\b")
_RUIDO = re.compile(r"^(PROFILE|Gtk-Message|Warning: USB events thread|pa_context_connect)")
_ERRO = re.compile(r"\b(ERRO|ERROR|Traceback|falhou|falha)\b")
_AVISO = re.compile(r"\b(AVISO|aviso|Warning|ATENCAO|desisti|caiu|travou|OFFLINE)\b|\brc=[1-9]|Connection (refused|failure)")


class _Estado:
    """Memoria entre linhas: tracebacks e o bloco de varias linhas do arecord."""
    def __init__(self):
        self.traceback = False


def classificar(linha, estado=None):
    """(nivel, tag, texto, importante) de uma linha crua. `nivel` e DEBUG/INFO/WARN/ERROR."""
    estado = estado or _Estado()

    # traceback: todas as linhas dele sao erro importante (a ultima, a do tipo da excecao, fecha)
    if estado.traceback:
        if linha[:1] in (" ", "\t") or not linha.strip():
            return "ERROR", "app", linha.strip(), True
        estado.traceback = False
        return "ERROR", "app", linha.strip(), True
    if linha.startswith("Traceback (most recent call last)"):
        estado.traceback = True
        return "ERROR", "app", linha.strip(), True

    m = _TAG.match(linha)
    tag, texto = (m.group(1), m.group(2)) if m else (None, linha)

    if tag is None:
        m = _DEPTH.match(linha)
        if m:
            quadros, erros = int(m.group(1)), int(m.group(2))
            if erros > 0 or quadros < 100:
                return "WARN", "profundidade", "%d quadros, %d erros em 5 s" % (quadros, erros), True
            return "DEBUG", "profundidade", linha, False
        if _RUIDO.match(linha) or _DUMP_ARECORD.match(linha):
            return "DEBUG", "app", linha, False
        m = _SENSOR.match(linha)
        if m:
            return "INFO", "sensor" if m.group(1) != "Rosto" else "rosto", linha, True
        tag = "app"
    else:
        if tag == "transcritor" and texto.startswith("descartado"):
            return "DEBUG", tag, texto, False
        if tag == "conversa" and (texto.startswith("audio recebido") or texto.startswith("audio descartado")):
            return "DEBUG", tag, texto, False
        if tag == "mic_vad":
            if "tentativa" in texto and "de religar" in texto and "arecord caiu" not in texto:
                return "DEBUG", tag, texto, False                # cauda do bloco de varias linhas
            if "arecord caiu" in texto:
                causa = "Unable to install hw params" if "Unable to install hw params" in texto else "erro"
                return "WARN", tag, "arecord caiu (%s; audio USB travado?)" % causa, True
        if tag == "transcritor" and texto.startswith("'"):
            return "INFO", tag, "ouviu " + texto, True            # frase reconhecida: o que importa

    if _ERRO.search(texto):
        return "ERROR", tag, texto, True
    if _AVISO.search(texto):
        return "WARN", tag, texto, True
    importante = tag in _TAGS_IMPORTANTES
    return "INFO", tag, texto, importante


_TAGS_IMPORTANTES = {"app", "conversa", "saudacao", "controle", "transcritor", "mic_vad", "sensor", "rosto"}


# ── registro ────────────────────────────────────────────────────────────
class _Registro:
    def __init__(self, logs_dir):
        self.logs_dir = logs_dir
        self._lock = threading.Lock()
        self._estado = _Estado()
        self._ultima = None                 # (chave, instante, repeticoes) da ultima linha importante
        try:
            os.makedirs(logs_dir, exist_ok=True)
        except OSError as e:
            sys.__stderr__.write("[camera_log] sem pasta de logs diarios (%s): %s\n" % (logs_dir, e))
            self.logs_dir = None

    def linha(self, bruta, destino):
        agora = time.time()
        with self._lock:
            nivel, tag, texto, importante = classificar(bruta, self._estado)
            ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(agora))
            fmt = "%s %-5s [%s] %s" % (ts, nivel, tag, texto)
            try:
                destino.write((colorir(fmt) if _e_terminal(destino) else fmt) + "\n")
                destino.flush()
            except (OSError, ValueError):
                pass
            if importante and self.logs_dir:
                self._diario(nivel, tag, texto, agora, ts)

    def _diario(self, nivel, tag, texto, agora, ts):
        chave = (nivel, tag, texto)
        if self._ultima and self._ultima[0] == chave and agora - self._ultima[1] < REPETIDA_JANELA_S:
            self._ultima = (chave, agora, self._ultima[2] + 1)
            return
        self._fechar_repeticao(agora)
        self._escrever(ts[:10], "%s %-5s [%s] %s" % (ts, nivel, tag, texto))
        self._ultima = (chave, agora, 0)

    def _fechar_repeticao(self, agora):
        if self._ultima and self._ultima[2] > 0:
            nivel, tag, _ = self._ultima[0]
            quando = time.localtime(self._ultima[1])
            ts = time.strftime("%Y-%m-%d %H:%M:%S", quando)
            self._escrever(ts[:10], "%s %-5s [%s] (a linha acima se repetiu mais %d vezes)"
                           % (ts, nivel, tag, self._ultima[2]))
        self._ultima = None

    def _escrever(self, dia, fmt):
        try:
            with open(os.path.join(self.logs_dir, dia + ".log"), "a", encoding="utf-8") as f:
                f.write(fmt + "\n")
        except OSError:
            pass

    def fechar(self):
        with self._lock:
            self._fechar_repeticao(time.time())


def _e_terminal(destino):
    try:
        return destino.isatty()
    except (AttributeError, ValueError):
        return False


class _Saida:
    """Substitui sys.stdout/sys.stderr: junta os pedacos ate fechar uma linha e a registra."""
    def __init__(self, original, registro):
        self._original = original
        self._registro = registro
        self._buf = ""
        self._lock = threading.Lock()

    def write(self, s):
        if not s:
            return 0
        with self._lock:
            self._buf += s
            linhas = []
            while "\n" in self._buf:
                linha, self._buf = self._buf.split("\n", 1)
                linhas.append(linha.rstrip("\r"))
        for linha in linhas:
            if linha.strip():
                self._registro.linha(linha, self._original)
        return len(s)

    def flush(self):
        try:
            self._original.flush()
        except (OSError, ValueError):
            pass

    def __getattr__(self, nome):        # isatty, fileno, encoding... seguem o original
        return getattr(self._original, nome)


_registro = None


def instalar(logs_dir=None):
    """Liga o log organizado neste processo (idempotente). Chamar logo no inicio do script."""
    global _registro
    if isinstance(sys.stdout, _Saida):
        return _registro
    _registro = _Registro(logs_dir or LOGS_DIR)
    sys.stdout = _Saida(sys.stdout, _registro)
    sys.stderr = _Saida(sys.stderr, _registro)
    atexit.register(_registro.fechar)
    podar(_registro.logs_dir)
    return _registro


def podar(logs_dir, dias=RETENCAO_DIAS):
    """Apaga arquivos diarios (AAAA-MM-DD.log) com mais de `dias` dias."""
    if not logs_dir:
        return 0
    limite = (datetime.date.today() - datetime.timedelta(days=dias)).isoformat()
    apagados = 0
    try:
        for nome in os.listdir(logs_dir):
            if re.match(r"^\d{4}-\d\d-\d\d\.log$", nome) and nome[:10] < limite:
                try:
                    os.remove(os.path.join(logs_dir, nome))
                    apagados += 1
                except OSError:
                    pass
    except OSError:
        pass
    return apagados
