"""
audio.py — Captura de áudio do microfone da Jetson.

Responsabilidades deste módulo:
  - localizar um dispositivo de entrada válido;
  - escolher uma taxa de amostragem que o hardware aceite;
  - gravar por uma duração fixa;
  - salvar em WAV (16-bit mono, formato aceito pela API da OpenAI);
  - detectar gravações silenciosas antes de gastar chamada de API;
  - apagar arquivos temporários.

Ainda não usado em produção: a Jetson atual não tem microfone plugado.
Fica pronto para quando o hardware chegar (ver jetson/audio_client.py).
"""

import os
import wave
import tempfile

import numpy as np
import sounddevice as sd

# --- Constantes de formato de áudio ---------------------------------------
# 16 kHz é o padrão para voz: cobre toda a faixa da fala humana (até ~8 kHz,
# pelo teorema de Nyquist) e gera arquivos 3x menores que 48 kHz. Menos bytes
# significa upload mais rápido, o que importa bastante numa Jetson no Wi-Fi.
TAXA_PREFERIDA = 16000
CANAIS = 1              # mono
LARGURA_AMOSTRA = 2     # 2 bytes por amostra = int16

# Abaixo deste RMS consideramos que o usuário não falou nada.
# Ajuste conforme o ganho do seu microfone (veja o teste da seção 2.3).
LIMIAR_SILENCIO = 150


class ErroDeAudio(Exception):
    """Erro previsível relacionado ao microfone ou à gravação."""
    pass


def listar_entradas():
    """
    Retorna [(indice, info_dict), ...] apenas dos dispositivos que possuem
    canais de ENTRADA. O sounddevice lista entradas e saídas na mesma lista,
    então precisamos filtrar por 'max_input_channels' > 0.
    """
    entradas = []
    for indice, info in enumerate(sd.query_devices()):
        if info["max_input_channels"] > 0:
            entradas.append((indice, info))
    return entradas


def escolher_dispositivo(indice_forcado=None):
    """
    Decide qual microfone usar.

    - Se indice_forcado for informado, valida e usa esse.
    - Caso contrário, usa a primeira entrada disponível.
    """
    entradas = listar_entradas()

    if not entradas:
        raise ErroDeAudio(
            "Nenhum microfone foi encontrado. "
            "Verifique a conexão USB e rode 'arecord -l' no terminal."
        )

    if indice_forcado is not None:
        indices_validos = [i for i, _ in entradas]
        if indice_forcado not in indices_validos:
            raise ErroDeAudio(
                f"O dispositivo {indice_forcado} não é uma entrada de áudio válida. "
                f"Entradas disponíveis: {indices_validos}"
            )
        return indice_forcado

    return entradas[0][0]


def escolher_taxa(indice_dispositivo):
    """
    Muitos microfones USB baratos não aceitam 16 kHz nativamente — só 44.1 kHz
    ou 48 kHz. Em vez de quebrar, testamos a taxa preferida e, se ela não for
    suportada, usamos a taxa nativa do dispositivo.

    A API da OpenAI aceita WAV em qualquer taxa, então gravar a 48 kHz
    funciona igual — apenas gera um arquivo maior.
    """
    try:
        sd.check_input_settings(
            device=indice_dispositivo,
            channels=CANAIS,
            samplerate=TAXA_PREFERIDA,
            dtype="int16",
        )
        return TAXA_PREFERIDA
    except Exception:
        info = sd.query_devices(indice_dispositivo)
        return int(info["default_samplerate"])


def calcular_rms(amostras):
    """
    RMS (Root Mean Square) = raiz da média dos quadrados.

    É a forma padrão de medir "quão alto" está um sinal de áudio. Usamos
    float32 no cálculo porque elevar int16 ao quadrado estoura o limite do
    tipo (32767^2 é muito maior que 32767) e daria resultado errado.

    Retorna um número entre 0 (silêncio absoluto) e ~32767 (saturado).
    """
    if amostras.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(amostras.astype(np.float32) ** 2)))


def salvar_wav(amostras, taxa, caminho):
    """
    Grava as amostras em disco no formato WAV usando o módulo 'wave' da
    biblioteca padrão — sem dependência extra.

    O cabeçalho WAV precisa saber: nº de canais, bytes por amostra e taxa.
    Depois disso, os dados são os bytes crus do array.
    """
    with wave.open(caminho, "wb") as arquivo:
        arquivo.setnchannels(CANAIS)
        arquivo.setsampwidth(LARGURA_AMOSTRA)
        arquivo.setframerate(taxa)
        arquivo.writeframes(amostras.tobytes())


def gravar(duracao_segundos=5, indice_dispositivo=None, caminho_saida=None):
    """
    Grava por uma duração fixa e devolve (caminho_do_wav, rms).

    Levanta ErroDeAudio em qualquer falha previsível.
    """
    dispositivo = escolher_dispositivo(indice_dispositivo)
    taxa = escolher_taxa(dispositivo)
    info = sd.query_devices(dispositivo)

    print(f"[INFO] Microfone detectado: {info['name']} (índice {dispositivo})", flush=True)
    print(f"[INFO] Taxa de amostragem: {taxa} Hz", flush=True)
    print(f"[INFO] Iniciando gravação ({duracao_segundos}s). Fale agora.", flush=True)

    total_de_amostras = int(duracao_segundos * taxa)

    try:
        # sd.rec() aloca o buffer e inicia a captura em segundo plano;
        # sd.wait() bloqueia até a gravação terminar.
        amostras = sd.rec(
            total_de_amostras,
            samplerate=taxa,
            channels=CANAIS,
            dtype="int16",
            device=dispositivo,
        )
        sd.wait()
    except sd.PortAudioError as erro:
        raise ErroDeAudio(
            f"Falha ao acessar o microfone: {erro}\n"
            "Verifique se outro programa não está usando o dispositivo."
        ) from erro

    print("[INFO] Gravação concluída.", flush=True)

    # sd.rec devolve shape (N, 1) para mono; achatamos para (N,).
    amostras = amostras.flatten()
    rms = calcular_rms(amostras)

    if caminho_saida is None:
        # delete=False porque queremos fechar o arquivo e reabrir depois
        # para enviar à API; nós mesmos apagamos com remover_arquivo().
        temporario = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        caminho_saida = temporario.name
        temporario.close()

    salvar_wav(amostras, taxa, caminho_saida)

    tamanho_kb = os.path.getsize(caminho_saida) / 1024
    print(f"[INFO] Arquivo salvo: {caminho_saida} ({tamanho_kb:.0f} KB, RMS={rms:.0f})",
          flush=True)

    return caminho_saida, rms


def esta_silencioso(rms, limiar=LIMIAR_SILENCIO):
    """Conveniência para o main.py decidir se vale a pena chamar a API."""
    return rms < limiar


def remover_arquivo(caminho):
    """
    Apaga o WAV temporário. Importante na Jetson: o armazenamento (cartão SD
    ou eMMC, dependendo do modelo) tem espaço limitado e um número finito de
    ciclos de escrita. Nunca deixe lixo acumulando.
    """
    try:
        if caminho and os.path.exists(caminho):
            os.remove(caminho)
    except OSError as erro:
        print(f"[AVISO] Não foi possível remover {caminho}: {erro}", flush=True)


if __name__ == "__main__":
    # Teste rápido: python audio.py
    try:
        caminho, rms = gravar(duracao_segundos=5)
        if esta_silencioso(rms):
            print("[AVISO] Áudio muito baixo — provavelmente nada foi captado.", flush=True)
        else:
            print("[INFO] Áudio captado com sucesso.", flush=True)
        print(f"[INFO] O arquivo foi mantido em: {caminho}", flush=True)
    except ErroDeAudio as erro:
        print(f"[ERRO] {erro}", flush=True)
