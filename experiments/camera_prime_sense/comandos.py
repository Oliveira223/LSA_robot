"""
Comandos que o terminal `camera` pode mandar pro Camera_Simples.py em execucao
(ver controle.py pro protocolo). Cada comando e uma funcao(arg: str) -> str;
ValueError vira mensagem de erro limpa pro usuario.

    status                       estado de cada parte do app
    saudar [on|off|texto ...|dist MIN MAX|intervalo S]
    falar <texto>                fala na caixa (testa a voz)
    digitar <texto>              injeta a frase como se tivesse sido falada

Reiniciar/parar o app NAO passa por aqui: e feito pelo proprio `camera`
chamando o camera-simples, pra funcionar mesmo se o app estiver travado.
"""
import os
import time


def _faixa(s):
    return "%.1f a %.1f m" % (s.dist_min, s.dist_max)


def registrar_comandos(servidor, args, saudador, ouvinte, transcritor, conversa,
                       leitor_depth, detector):
    inicio = time.time()

    def cmd_status(_arg):
        linhas = ["app: rodando (pid %d, ha %s)" % (os.getpid(), _duracao(time.time() - inicio))]

        if leitor_depth is None:
            linhas.append("profundidade: desligada")
        else:
            ok = leitor_depth.mapa() is not None
            linhas.append("profundidade: " + ("ok" if ok else
                          "SEM QUADROS (distancia indisponivel; replugue o cabo USB)"))

        if detector is None:
            linhas.append("rostos: detector desligado")
        else:
            linhas.append("rostos: %d no quadro" % len(detector.alvos()))

        if ouvinte is None:
            linhas.append("microfone: desligado")
        else:
            nivel, limiar, gravando, vivo = ouvinte.estado()
            if not vivo:
                linhas.append("microfone: OFFLINE (audio USB da PrimeSense travado; "
                              "so um replug do cabo resolve)")
            else:
                linhas.append("microfone: ok (nivel %d, limiar %d%s)"
                              % (nivel, limiar, ", captando fala" if gravando else ""))

        if transcritor is None:
            linhas.append("transcricao: desligada")
        else:
            linhas.append("transcricao: %s" % transcritor.estado())
        if conversa is not None:
            linhas.append("cerebro (Jetson 1): " + ("conectado" if conversa.conectado()
                                                     else "desconectado"))
        linhas.append("saudacao: " + _estado_saudacao(saudador))
        return "\n".join(linhas)

    def cmd_saudar(arg):
        partes = arg.split()
        if not partes:
            return "saudacao: " + _estado_saudacao(saudador)
        acao, resto = partes[0].lower(), partes[1:]

        if acao in ("on", "off"):
            if acao == "on" and not saudador.disponivel:
                raise ValueError("a saudacao precisa da deteccao de rosto e da profundidade "
                                 "(o app foi aberto com --no-faces ou --no-distancia)")
            saudador.ligar(acao == "on")
            return "saudacao " + ("LIGADA" if saudador.ativo else "desligada") \
                   + " (%s, %r)" % (_faixa(saudador), saudador.texto)
        if acao == "texto":
            texto = arg[len("texto"):].strip()
            if not texto:
                raise ValueError("uso: saudar texto <frase>")
            saudador.texto = texto
            return "frase da saudacao: %r" % texto
        if acao == "dist":
            try:
                minimo, maximo = float(resto[0]), float(resto[1])
            except (IndexError, ValueError):
                raise ValueError("uso: saudar dist <min> <max>   (metros, ex.: saudar dist 0.8 1.2)")
            if not 0 <= minimo < maximo <= 6:
                raise ValueError("a faixa precisa ter 0 <= min < max <= 6 m")
            saudador.dist_min, saudador.dist_max = minimo, maximo
            return "faixa da saudacao: " + _faixa(saudador)
        if acao == "intervalo":
            try:
                segundos = float(resto[0])
            except (IndexError, ValueError):
                raise ValueError("uso: saudar intervalo <segundos>")
            if segundos < 1:
                raise ValueError("o intervalo minimo e 1 s")
            saudador.intervalo_s = segundos
            return "intervalo entre saudacoes: %.0f s" % segundos
        raise ValueError("uso: saudar [on|off|texto <frase>|dist <min> <max>|intervalo <s>]")

    def cmd_falar(arg):
        if not arg:
            raise ValueError("uso: falar <texto>")
        return saudador.falar(arg)

    def cmd_digitar(arg):
        if not arg:
            raise ValueError("uso: digitar <texto>")
        if transcritor is None:
            raise ValueError("a transcricao esta desligada (--stt off, --no-mic ou mic indisponivel "
                             "na abertura)")
        transcritor.enviar_texto(arg)
        if conversa is not None and not conversa.conectado():
            return "enviado, mas a Jetson 1 (cerebro) esta desconectada: nao vai haver resposta"
        return "enviado ao cerebro: %r" % arg

    servidor.registrar("status", cmd_status)
    servidor.registrar("saudar", cmd_saudar)
    servidor.registrar("falar", cmd_falar)
    servidor.registrar("digitar", cmd_digitar)


def _estado_saudacao(s):
    if not s.disponivel:
        return "indisponivel (sem deteccao de rosto ou profundidade)"
    return "%s | faixa %s | intervalo %.0f s | frase %r" % (
        "LIGADA" if s.ativo else "desligada", _faixa(s), s.intervalo_s, s.texto)


def _duracao(s):
    s = int(s)
    if s < 60:
        return "%d s" % s
    if s < 3600:
        return "%d min" % (s // 60)
    return "%d h %d min" % (s // 3600, s % 3600 // 60)
