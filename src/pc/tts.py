"""
tts.py — síntese de voz (Text-To-Speech) no PC, com espeak-ng.

Roda 100% local e offline: espeak-ng é um pacote de sistema (apt), sem
modelo para baixar — o que evita repetir o problema do faster-whisper na
rede da PUC (o backend xet trava em download longo). A voz é robótica, o
que combina com a estética do robô.

Isola o resto do código do detalhe do motor de voz: quem chama usa
sintetizar(texto) -> bytes (WAV). Trocar espeak-ng por piper (voz neural,
mais natural, mas com modelo para baixar) é mexer só aqui, mantendo a
mesma função sintetizar(...).

A voz é sintetizada no PC de propósito: a Jetson é só "ouvido e boca" —
recebe o WAV pronto pelo socket (mensagem AUDIO) e toca no speaker, sem
motor de TTS nem modelo a bordo. Ver docs/roadmap-comunicacao.md (Fase 4).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile

# Voz e ritmo do espeak-ng. Trocar sem mexer no código:
#   export LSA_TTS_VOZ=pt      (pt-br é o padrão; "pt" = português de Portugal)
#   export LSA_TTS_WPM=175     (palavras por minuto; 175 é o default do espeak)
_VOZ = os.environ.get("LSA_TTS_VOZ", "pt-br")
_WPM = os.environ.get("LSA_TTS_WPM", "160")
_BIN = os.environ.get("LSA_TTS_BIN", "espeak-ng")


class ErroDeTTS(Exception):
    """Falha previsível ao sintetizar voz (motor ausente, texto vazio)."""


def disponivel() -> bool:
    """True se o binário do espeak-ng está no PATH."""
    return shutil.which(_BIN) is not None


def sintetizar(texto: str) -> bytes:
    """
    Converte `texto` em fala e devolve os bytes de um arquivo WAV
    (PCM 16-bit mono, ~22 kHz — o que o espeak-ng gera com -w).

    Levanta ErroDeTTS se o texto for vazio ou o motor não estiver instalado.
    """
    texto = (texto or "").strip()
    if not texto:
        raise ErroDeTTS("texto vazio — nada para sintetizar")

    if not disponivel():
        raise ErroDeTTS(
            f"'{_BIN}' nao encontrado. Instale com:\n"
            "  sudo apt install -y espeak-ng"
        )

    # -w escreve o áudio num WAV em vez de tocar; --stdin lê o texto do
    # stdin (evita passar texto arbitrário como argumento de linha).
    tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    tmp.close()
    try:
        proc = subprocess.run(
            [_BIN, "-v", _VOZ, "-s", str(_WPM), "-w", tmp.name, "--stdin"],
            input=texto.encode("utf-8"),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if proc.returncode != 0:
            erro = proc.stderr.decode("utf-8", "replace").strip()
            raise ErroDeTTS(f"espeak-ng falhou (codigo {proc.returncode}): {erro}")
        with open(tmp.name, "rb") as f:
            dados = f.read()
    finally:
        try:
            os.remove(tmp.name)
        except OSError:
            pass

    if len(dados) < 100:
        raise ErroDeTTS("espeak-ng gerou um WAV vazio")
    return dados


if __name__ == "__main__":
    # Teste rápido:  python -m pc.tts "texto a falar"  > saida.wav
    import sys

    frase = " ".join(sys.argv[1:]) or "Olá, eu sou o robô."
    try:
        wav = sintetizar(frase)
    except ErroDeTTS as e:
        print(f"[ERRO] {e}", file=sys.stderr)
        raise SystemExit(1)
    sys.stdout.buffer.write(wav)
    print(f"[tts] {len(wav)} bytes de WAV para {frase!r}", file=sys.stderr)
