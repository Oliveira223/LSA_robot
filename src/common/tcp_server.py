"""
tcp_server.py — laco comum de aceitar conexoes dos servidores TCP deste
projeto (pc.server, pc.server_chat, pc.server_voz, jetson.tts_server).

Todos fazem a mesma coisa em volta do socket: cria, liga SO_REUSEADDR,
bind/listen, aceita um cliente de cada vez e chama uma funcao de
atendimento ate ela retornar (cliente desconectou ou deu erro) — so o que
essa funcao faz por mensagem muda de servidor pra servidor. `servir()`
concentra essa parte repetida; quem chama so passa `atender(conexao)`.

Sem "from __future__ import annotations": jetson.tts_server roda no
Python 3.6 da Jetson, que nao aceita esse import.
"""

import socket


def servir(host, porta, atender, prefixo="[servidor]"):
    """Escuta em host:porta e chama atender(conexao) pra cada cliente.

    Atende um cliente de cada vez: quando atender() retorna, volta a
    aceitar o proximo. Roda ate Ctrl-C.
    """
    servidor = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    # Permite reabrir a porta logo apos fechar o servidor, sem esperar o TIME_WAIT.
    servidor.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    servidor.bind((host, porta))
    servidor.listen(1)
    print("%s escutando em %s:%d (Ctrl-C para sair)" % (prefixo, host, porta))

    try:
        while True:
            conexao, endereco = servidor.accept()
            print("%s cliente conectado: %s:%d" % (prefixo, endereco[0], endereco[1]))
            with conexao:
                atender(conexao)
            print("%s aguardando novo cliente..." % prefixo)
    except KeyboardInterrupt:
        print("\n%s encerrando" % prefixo)
    finally:
        servidor.close()
