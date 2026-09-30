"""
Comandos que o terminal `camera` pode mandar pro Camera_Simples.py em execucao
(ver controle.py pro protocolo). Cada comando e uma funcao(arg: str) -> str;
ValueError vira mensagem de erro limpa pro usuario.

    status                       estado de cada parte do app
    saudar [on|off|texto ...|dist MIN MAX|intervalo S]
    falar <texto>                fala na caixa (testa a voz)
    digitar <texto>              injeta a frase como se tivesse sido falada
    reiniciar mic                recria o ouvinte do microfone dentro do app
    chat [on|off]                liga/desliga o envio da fala do microfone ao cerebro
    shush                        interrompe a fala do robo agora
    ouvir <seq>                  (interno) mensagens novas, pra transcrever/conversar

Reiniciar/parar o app NAO passa por aqui: e feito pelo proprio `camera`
chamando o camera-simples, pra funcionar mesmo se o app estiver travado.
"""
import json
import os
import time


def _faixa(s):
    return "%.1f a %.1f m" % (s.dist_min, s.dist_max)


def registrar_comandos(servidor, args, saudador, mic, transcritor, conversa,
                       leitor_depth, detector, criar_ouvinte=None, erro_de_mic=Exception):
    """`mic` e um holder com o atributo `ouvinte` (o app le dele a cada quadro),
    pra o comando `reiniciar mic` poder trocar o ouvinte sem reabrir o app.
    `criar_ouvinte` e uma funcao sem argumentos que devolve um OuvinteVAD novo
    (None quando o mic esta desligado por flag)."""
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

        linhas.append("microfone: " + _estado_mic(mic.ouvinte, criar_ouvinte is not None))

        if transcritor is None:
            linhas.append("transcricao: desligada")
        else:
            linhas.append("transcricao: %s%s" % (
                transcritor.estado(), "" if mic.ouvinte is not None else " (so texto, sem microfone)"))
        if conversa is not None:
            linhas.append("cerebro (Jetson 1): " + ("conectado" if conversa.conectado()
                                                     else "desconectado"))
        if conversa is not None and conversa.conectado():
            linhas.append("voz: " + ("robo (Piper, Jetson 1)" if conversa.suporta_falar() else
                          "local (voz robotica; o servidor da Jetson 1 esta desatualizado)"))
        if transcritor is not None and conversa is not None:
            linhas.append("conversa por voz: " + ("LIGADA (o que o microfone ouve vai ao cerebro)"
                          if transcritor.conversa_ativa else
                          "desligada (o microfone so transcreve; `type` ainda responde)"))
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

    def cmd_reiniciar_mic(_arg):
        if criar_ouvinte is None:
            raise ValueError("o microfone esta desligado neste modo (--no-mic ou --mic-mock)")
        velho = mic.ouvinte
        mic.ouvinte = None
        if transcritor is not None:
            transcritor.trocar_ouvinte(None)
        if velho is not None:
            velho.parar()                      # solta o arecord antes de abrir outro
        try:
            novo = criar_ouvinte()
        except erro_de_mic as e:
            raise ValueError("nao consegui abrir o microfone: %s" % e)
        mic.ouvinte = novo
        if transcritor is not None:
            transcritor.trocar_ouvinte(novo)
        # "vivo" sozinho engana (o ouvinte tenta religar 5x antes de desistir):
        # confirma que chegou audio de verdade.
        limite = time.time() + 8.0
        while time.time() < limite:
            if not novo.estado()[3]:
                break
            if len(novo.amostras_recentes()) > 0:
                nivel = novo.estado()[0]
                return "microfone religado e captando (nivel %d)" % nivel
            time.sleep(0.3)
        raise ValueError("o microfone nao voltou: nenhum audio chegou. O audio USB da "
                         "PrimeSense esta travado (kernel: 'cannot set freq 48000 to ep 0x84'); "
                         "so um replug do cabo resolve. Enquanto isso, use 'digitar'/'conversar'.")

    def cmd_ouvir(arg):
        if transcritor is None:
            raise ValueError("a transcricao esta desligada (--stt off)")
        try:
            seq = int(arg or "-1")
        except ValueError:
            raise ValueError("uso: ouvir <seq>")
        if seq < 0:                            # primeira chamada: so ancora no "agora"
            return json.dumps({"seq": transcritor.registro_desde(0)[0], "itens": [], "parcial": "",
                               "aguardando": False})
        ultimo, itens, parcial = transcritor.registro_desde(seq)
        return json.dumps({"seq": ultimo, "itens": itens, "parcial": parcial,
                           "aguardando": bool(conversa is not None and conversa.aguardando())},
                          ensure_ascii=False)

    def cmd_chat(arg):
        if transcritor is None or conversa is None:
            raise ValueError("a conversa com o cerebro esta desligada (--no-cerebro, --stt off)")
        acao = arg.strip().lower()
        if acao in ("on", "off"):
            transcritor.conversa_ativa = acao == "on"
            if acao == "off":
                conversa.interromper()          # desligar tambem corta o que o robo estiver falando
        elif acao:
            raise ValueError("uso: chat [on|off]")
        return "conversa por voz: " + ("LIGADA" if transcritor.conversa_ativa else
                                       "DESLIGADA (o microfone so transcreve; `type` ainda responde)")

    def cmd_shush(arg):
        if arg:
            raise ValueError("shush nao tem argumentos: ele so corta a fala de agora (a proxima "
                             "resposta toca normal). Pra desligar a conversa use: chat off")
        if conversa is None:
            raise ValueError("nao ha conversa com o cerebro (--no-cerebro)")
        if conversa.interromper():
            return "interrompido"
        if conversa.descartando():
            return "nada tocando agora, mas a resposta ainda esta chegando: o resto do audio sera ignorado"
        return "o robo nao estava falando"

    servidor.registrar("status", cmd_status)
    servidor.registrar("chat", cmd_chat)
    servidor.registrar("shush", cmd_shush)
    servidor.registrar("reiniciar_mic", cmd_reiniciar_mic)
    servidor.registrar("ouvir", cmd_ouvir)
    servidor.registrar("saudar", cmd_saudar)
    servidor.registrar("falar", cmd_falar)
    servidor.registrar("digitar", cmd_digitar)


def _estado_mic(ouvinte, pode_reiniciar):
    if ouvinte is None:
        return "indisponivel" + (" (tente: reiniciar mic)" if pode_reiniciar else " (desligado)")
    nivel, limiar, gravando, vivo = ouvinte.estado()
    if not vivo:
        return ("OFFLINE (o ouvinte desistiu de religar; audio USB da PrimeSense travado - "
                "so um replug do cabo resolve)")
    if len(ouvinte.amostras_recentes()) == 0:
        return "SEM AUDIO chegando (tentando religar; se persistir, replugue o cabo USB)"
    return "ok (nivel %d, limiar %d%s)" % (nivel, limiar, ", captando fala" if gravando else "")


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
