"""
Canal de controle do Camera_Simples.py: um socket Unix (SOCKET) por onde o
terminal `camera` (src/jetson/bin/camera) manda comandos pro app em execucao —
ligar/desligar a saudacao, ver o status, mandar falar ou digitar uma frase —
sem reiniciar nada (reiniciar e justamente o que gasta o audio USB da
PrimeSense).

Protocolo: uma linha JSON por pedido e uma por resposta.
    -> {"cmd": "saudar", "arg": "on"}
    <- {"ok": true, "msg": "saudacao ligada"}
Um handler devolve o texto da resposta; levantar ValueError vira uma resposta
de erro limpa (ok=false) com a mensagem, sem derrubar o app.

So aceita conexoes do mesmo usuario (socket com permissao 0600).

Compatibilidade: Python 3.6 da Jetson.
"""
import json
import os
import socket
import threading

SOCKET = "/tmp/camera.sock"
TIMEOUT_CONEXAO_S = 60.0      # `falar` pode levar alguns segundos


class ServidorControle:
    def __init__(self, caminho=SOCKET):
        self._caminho = caminho
        self._handlers = {}
        self._sock = None
        self._rodando = False

    def registrar(self, nome, funcao):
        """funcao(arg: str) -> str (resposta) ou levanta ValueError (erro)."""
        self._handlers[nome] = funcao

    def iniciar(self):
        try:
            os.unlink(self._caminho)       # sobra de um app que morreu sem limpar
        except FileNotFoundError:
            pass
        self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._sock.bind(self._caminho)
        os.chmod(self._caminho, 0o600)
        self._sock.listen(4)
        self._rodando = True
        threading.Thread(target=self._aceitar, daemon=True).start()

    def parar(self):
        self._rodando = False
        try:
            if self._sock is not None:
                self._sock.close()
            os.unlink(self._caminho)
        except OSError:
            pass

    def _aceitar(self):
        while self._rodando:
            try:
                conexao, _ = self._sock.accept()
            except OSError:
                return
            threading.Thread(target=self._atender, args=(conexao,), daemon=True).start()

    def _atender(self, conexao):
        try:
            conexao.settimeout(TIMEOUT_CONEXAO_S)
            dados = b""
            while not dados.endswith(b"\n") and len(dados) < 65536:
                pedaco = conexao.recv(4096)
                if not pedaco:
                    break
                dados += pedaco
            resposta = self._responder(dados)
            conexao.sendall((json.dumps(resposta, ensure_ascii=False) + "\n").encode("utf-8"))
        except (OSError, ValueError) as e:
            print("[controle] erro na conexao: %s" % e, flush=True)
        finally:
            conexao.close()

    def _responder(self, dados):
        try:
            pedido = json.loads(dados.decode("utf-8"))
            nome, arg = pedido["cmd"], str(pedido.get("arg", "")).strip()
        except (ValueError, KeyError, TypeError):
            return {"ok": False, "msg": "pedido invalido"}
        funcao = self._handlers.get(nome)
        if funcao is None:
            return {"ok": False, "msg": "comando desconhecido: %s" % nome}
        try:
            return {"ok": True, "msg": funcao(arg)}
        except ValueError as e:
            return {"ok": False, "msg": str(e)}
        except Exception as e:   # um comando com bug nao pode derrubar o app
            print("[controle] %s falhou: %r" % (nome, e), flush=True)
            return {"ok": False, "msg": "erro interno em '%s': %r" % (nome, e)}
