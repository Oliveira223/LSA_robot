"""
protocol.py — enquadramento de mensagens sobre TCP.

TCP entrega um fluxo contínuo de bytes, sem fronteiras: uma chamada recv()
pode trazer meia mensagem, uma mensagem inteira ou várias grudadas. Para
saber onde cada mensagem termina, cada uma é enviada como:

    [ 4 bytes: tamanho do payload, big-endian, sem sinal ] [ payload ]

Aqui o payload é texto UTF-8. Nas fases seguintes do projeto o mesmo
esquema serve para bytes de áudio — só muda o conteúdo do payload.
"""

from __future__ import annotations

import socket
import struct

_HEADER = struct.Struct(">I")   # unsigned int, 4 bytes, big-endian
MAX_MSG = 64 * 1024 * 1024      # teto de sanidade: 64 MiB por mensagem


def send_msg(sock: socket.socket, texto: str) -> None:
    """Envia uma string como uma mensagem enquadrada (tamanho + payload)."""
    payload = texto.encode("utf-8")
    sock.sendall(_HEADER.pack(len(payload)) + payload)


def recv_msg(sock: socket.socket) -> str | None:
    """
    Recebe uma mensagem enquadrada e devolve a string.

    Devolve None se o outro lado fechou a conexão de forma limpa.
    Levanta ValueError se o cabeçalho anunciar um tamanho absurdo
    (sinal de dados corrompidos ou de que os dois lados estão
    falando protocolos diferentes).
    """
    header = _recv_exato(sock, _HEADER.size)
    if header is None:
        return None
    (tamanho,) = _HEADER.unpack(header)
    if tamanho > MAX_MSG:
        raise ValueError(f"mensagem grande demais: {tamanho} bytes")
    payload = _recv_exato(sock, tamanho)
    if payload is None:
        return None
    return payload.decode("utf-8")


def _recv_exato(sock: socket.socket, n: int) -> bytes | None:
    """Lê exatamente n bytes do socket, ou None se a conexão fechar antes."""
    buf = bytearray()
    while len(buf) < n:
        pedaco = sock.recv(n - len(buf))
        if not pedaco:          # conexão fechada pelo outro lado
            return None
        buf.extend(pedaco)
    return bytes(buf)
