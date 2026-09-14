#!/bin/bash
# Reverte a instalação.
# Uso:  sudo ./uninstall.sh
set -e
KVER="$(uname -r)"
echo ">> descarregando módulo"
modprobe -r snd-usb-audio-jieli 2>/dev/null || rmmod snd-usb-audio-jieli 2>/dev/null || true
echo ">> removendo arquivos"
rm -f "/lib/modules/${KVER}/updates/snd-usb-audio-jieli.ko"
rm -f /etc/modules-load.d/snd-usb-audio-jieli.conf
depmod -a "${KVER}"
echo ">> feito. O dongle volta a não ter driver (comportamento original)."
