"""
protocol.py — enquadramento de mensagens sobre TCP.

TCP entrega um fluxo contínuo de bytes, sem fronteiras: uma chamada recv()
pode trazer meia mensagem, uma mensagem inteira ou várias grudadas. Para
saber onde cada mensagem termina — e o que ela é — cada uma vai como:

    [ 1 byte: tipo ] [ 4 bytes: tamanho do payload, big-endian, sem sinal ] [ payload ]

    tipo 0x01 = TEXTO  → payload em UTF-8
    tipo 0x02 = AUDIO  → payload = bytes de um arquivo WAV

Até a etapa (b) o payload era sempre texto. A etapa (d) precisa mandar
áudio pelo mesmo canal, e o receptor precisa distinguir os dois — daí o
byte de tipo na frente. O enquadramento por tamanho continua igual: é o
que garante que um WAV grande chegue inteiro, mesmo picado em vários recv().
"""

from __future__ import annotations

import socket
import struct
from collections import namedtuple

# ">" = big-endian; "B" = 1 byte sem sinal (tipo); "I" = 4 bytes sem sinal (tamanho).
_HEADER = struct.Struct(">BI")
MAX_MSG = 64 * 1024 * 1024      # teto de sanidade: 64 MiB por mensagem (cobre WAV curto)

TEXTO = 0x01
AUDIO = 0x02

_NOMES_TIPO = {TEXTO: "TEXTO", AUDIO: "AUDIO"}


class Mensagem(namedtuple("Mensagem", "tipo dados")):
    """
    Uma mensagem recebida: `tipo` (TEXTO/AUDIO) e `dados` (sempre bytes crus).

    `.texto` decodifica os bytes como UTF-8 — só faz sentido quando
    `tipo == TEXTO`; levanta ValueError nos outros casos para não mascarar
    um erro de fluxo (ex.: tratar áudio como se fosse texto).
    """
    __slots__ = ()

    @property
    def texto(self) -> str:
        if self.tipo != TEXTO:
            raise ValueError(
                f"mensagem do tipo {_NOMES_TIPO.get(self.tipo, self.tipo)} "
                "não é texto"
            )
        return self.dados.decode("utf-8")


def _enviar(sock: socket.socket, tipo: int, payload: bytes) -> None:
    if len(payload) > MAX_MSG:
        raise ValueError(f"mensagem grande demais: {len(payload)} bytes")
    sock.sendall(_HEADER.pack(tipo, len(payload)) + payload)


def send_texto(sock: socket.socket, texto: str) -> None:
    """Envia uma string como uma mensagem enquadrada do tipo TEXTO."""
    _enviar(sock, TEXTO, texto.encode("utf-8"))


def send_audio(sock: socket.socket, dados: bytes) -> None:
    """Envia bytes de um WAV como uma mensagem enquadrada do tipo AUDIO."""
    _enviar(sock, AUDIO, dados)


def recv_msg(sock: socket.socket) -> Mensagem | None:
    """
    Recebe uma mensagem enquadrada e devolve um Mensagem(tipo, dados).

    Devolve None se o outro lado fechou a conexão de forma limpa.
    Levanta ValueError se o cabeçalho anunciar um tipo desconhecido ou um
    tamanho absurdo (sinal de dados corrompidos ou de que os dois lados
    estão falando protocolos diferentes).
    """
    header = _recv_exato(sock, _HEADER.size)
    if header is None:
        return None
    tipo, tamanho = _HEADER.unpack(header)
    if tipo not in _NOMES_TIPO:
        raise ValueError(f"tipo de mensagem desconhecido: {tipo}")
    if tamanho > MAX_MSG:
        raise ValueError(f"mensagem grande demais: {tamanho} bytes")
    payload = _recv_exato(sock, tamanho)
    if payload is None:
        return None
    return Mensagem(tipo, payload)


def _recv_exato(sock: socket.socket, n: int) -> bytes | None:
    """Lê exatamente n bytes do socket, ou None se a conexão fechar antes."""
    buf = bytearray()
    while len(buf) < n:
        pedaco = sock.recv(n - len(buf))
        if not pedaco:          # conexão fechada pelo outro lado
            return None
        buf.extend(pedaco)
    return bytes(buf)
