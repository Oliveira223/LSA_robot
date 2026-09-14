#!/bin/bash
# Teste rápido: carrega o módulo direto do diretório de build e mostra o resultado.
# Uso:  sudo ./load-test.sh
set -e
KO="$(dirname "$0")/src/snd-usb-audio-jieli.ko"

echo ">> removendo instância anterior (se houver)"
rmmod snd-usb-audio-jieli 2>/dev/null || true

echo ">> insmod $KO"
insmod "$KO"

echo ">> dmesg (últimas linhas de áudio USB)"
dmesg | grep -iE "snd-usb-audio-jieli|1-2\.3|JieLi|usb.*Audio" | tail -20 || true

echo
echo ">> placas ALSA"
cat /proc/asound/cards

echo
echo ">> aplay -l"
aplay -l

echo
echo ">> Para testar som (ajuste o nº do card, ex. 2):"
echo "   speaker-test -D plughw:2,0 -c 2 -t wav -l 1"
