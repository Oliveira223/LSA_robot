"""
cerebro.py — a camada de "resposta" do pipeline: o que decide o que volta
para a Jetson depois que uma mensagem chega (por ora, sempre texto; mais
adiante, texto já transcrito de áudio).

Por enquanto quem responde é um OPERADOR HUMANO sentado neste terminal do
PC — simulando o "outro usuário" da conversa enquanto não há IA. Tanto
server_chat.py (mensagens de texto) quanto server_voz.py (mensagens de
áudio, depois de transcritas) chamam a mesma função `responder()` daqui.
Trocar o operador por uma IA de verdade é mudar só este arquivo — os dois
servidores não precisam mudar. Ver docs/roadmap-ia-conversacional.md para
os próximos passos (regras → API → memória de conversa).
"""

from __future__ import annotations


def responder(texto_recebido: str) -> str:
    """
    Mostra a mensagem recebida e devolve a resposta digitada pelo operador.

    `texto_recebido` já é texto puro nesse ponto: server_chat.py manda o
    texto que a Jetson escreveu; server_voz.py manda o texto que o
    faster-whisper transcreveu do áudio. Esta função não sabe (nem precisa
    saber) de onde o texto veio.
    """
    print(f"\n[usuario] {texto_recebido!r}")
    if not texto_recebido:
        print("[operador] (mensagem vazia)")
    try:
        return input("[operador] resposta> ")
    except EOFError:
        print()
        return ""
