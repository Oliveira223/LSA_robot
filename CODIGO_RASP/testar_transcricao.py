"""
testar_transcricao.py — Grava uma frase e mostra a transcrição.

Uso:
    python testar_transcricao.py
"""

import sys

import audio
import config
import openai_client


def main():
    # 1) Valida a chave ANTES de gravar. Não faz sentido pedir para o
    #    usuário falar 5 segundos e só depois avisar que falta a API Key.
    try:
        config.obter_api_key()
    except config.ErroDeConfiguracao as erro:
        print(f"[ERRO] {erro}")
        return 1

    print("=" * 60)
    print("TESTE DE TRANSCRIÇÃO")
    print("=" * 60)
    print("Fale uma frase clara quando aparecer 'Iniciando gravação'.")
    print()

    caminho = None
    try:
        # 2) Grava
        caminho, rms = audio.gravar(duracao_segundos=config.DURACAO_GRAVACAO)

        # 3) Se ninguém falou, não gasta chamada de API
        if audio.esta_silencioso(rms):
            print(f"[AVISO] Nenhuma fala detectada (RMS={rms:.0f}).")
            print("        Nada foi enviado para a API.")
            return 0

        # 4) Transcreve
        texto = openai_client.transcrever(caminho)

        print()
        if not texto:
            print("Transcrição:")
            print("  (a API não identificou fala no áudio)")
        else:
            print("Transcrição:")
            print(f"  {texto}")

    except audio.ErroDeAudio as erro:
        print(f"[ERRO] {erro}")
        return 1
    except openai_client.ErroDaAPI as erro:
        print(f"[ERRO] {erro}")
        return 1
    except KeyboardInterrupt:
        print("\n[INFO] Interrompido pelo usuário.")
        return 0
    finally:
        # 5) O 'finally' roda sempre — com sucesso, com erro ou com Ctrl+C.
        #    É o lugar certo para limpeza: garante que o WAV temporário
        #    não fique ocupando espaço no cartão SD.
        audio.remover_arquivo(caminho)

    return 0


if __name__ == "__main__":
    sys.exit(main())
