"""
cli.py — pequenos helpers de terminal compartilhados pelos clientes
push-to-talk (jetson.audio_client, pc.push_to_talk, pc.voice_client).
"""

from __future__ import annotations


def ler(prompt: str = "") -> str | None:
    """input() que devolve None em EOF/Ctrl-C em vez de estourar."""
    try:
        return input(prompt)
    except (EOFError, KeyboardInterrupt):
        print()
        return None


def sair(resposta: str | None) -> bool:
    """True se a entrada pede pra sair (EOF/Ctrl-C, 'q', 'sair', 'quit', 'exit')."""
    return resposta is None or resposta.strip().lower() in ("q", "sair", "quit", "exit")
