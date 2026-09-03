"""
testar_microfone.py — Diagnóstico do microfone antes de plugar no pipeline.

Uso (a partir de src/):
    python -m rasp.testar_microfone            # lista dispositivos e grava 5s
    python -m rasp.testar_microfone 2          # força o dispositivo de índice 2
"""

import sys

import sounddevice as sd

from rasp import audio


def mostrar_dispositivos():
    print("=" * 60)
    print("DISPOSITIVOS DE ÁUDIO DETECTADOS")
    print("=" * 60)

    todos = sd.query_devices()
    if len(todos) == 0:
        print("Nenhum dispositivo de áudio encontrado.")
        return

    for indice, info in enumerate(todos):
        entradas = info["max_input_channels"]
        saidas = info["max_output_channels"]
        tipo = "ENTRADA" if entradas > 0 else "saída"
        marcador = "  <-- microfone" if entradas > 0 else ""
        print(f"[{indice}] {info['name']}")
        print(f"     tipo={tipo}  in={entradas}  out={saidas}  "
              f"taxa_padrao={int(info['default_samplerate'])} Hz{marcador}")
    print()


def main():
    mostrar_dispositivos()

    # Se o usuário passou um índice como argumento, usamos ele.
    indice = None
    if len(sys.argv) > 1:
        try:
            indice = int(sys.argv[1])
        except ValueError:
            print(f"[ERRO] '{sys.argv[1]}' não é um número de dispositivo válido.")
            return 1

    print("=" * 60)
    print("TESTE DE GRAVAÇÃO")
    print("=" * 60)

    try:
        caminho, rms = audio.gravar(duracao_segundos=5,
                                    indice_dispositivo=indice,
                                    caminho_saida="teste_python.wav")
    except audio.ErroDeAudio as erro:
        print(f"[ERRO] {erro}")
        return 1

    print()
    print("-" * 60)
    print(f"Nível medido (RMS): {rms:.0f}")

    if rms < 50:
        print("DIAGNÓSTICO: praticamente silêncio.")
        print("  -> Rode 'alsamixer', tecle F6 e F4, e aumente o ganho de captura.")
        print("  -> Confirme que aparece CAPTURE em vermelho sob a barra.")
    elif rms < audio.LIMIAR_SILENCIO:
        print("DIAGNÓSTICO: sinal muito fraco.")
        print("  -> Fale mais perto do microfone ou aumente o ganho.")
    elif rms > 15000:
        print("DIAGNÓSTICO: sinal muito alto, risco de distorção.")
        print("  -> Reduza o ganho no alsamixer ou afaste-se do microfone.")
    else:
        print("DIAGNÓSTICO: nível bom. O microfone está pronto.")

    print("-" * 60)
    print(f"Arquivo gravado em: {caminho}")
    print("Ouça com:  aplay teste_python.wav      (no Pi)")
    print("           paplay teste_python.wav     (no PC, se aplay não existir)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
