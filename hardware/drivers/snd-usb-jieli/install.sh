#!/bin/bash
# Instala o módulo de forma persistente (sobrevive a reboot).
# Uso:  sudo ./install.sh
set -e
SRC="$(cd "$(dirname "$0")" && pwd)/src"
KVER="$(uname -r)"
DEST="/lib/modules/${KVER}/updates"

echo ">> copiando snd-usb-audio-jieli.ko para ${DEST}"
install -D -m 0644 "${SRC}/snd-usb-audio-jieli.ko" "${DEST}/snd-usb-audio-jieli.ko"

echo ">> depmod -a ${KVER}"
depmod -a "${KVER}"

echo ">> registrando carga automática no boot"
echo "snd-usb-audio-jieli" > /etc/modules-load.d/snd-usb-audio-jieli.conf

echo ">> carregando agora"
modprobe -r snd-usb-audio-jieli 2>/dev/null || true
modprobe snd-usb-audio-jieli

echo
echo ">> estado:"
lsmod | grep snd_usb_audio_jieli || true
cat /proc/asound/cards
echo
echo "OK. Se o dongle já estava plugado, ele deve aparecer como um novo card acima."
echo "Reconecte o dongle uma vez para confirmar o hotplug automático."
