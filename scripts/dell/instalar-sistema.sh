#!/usr/bin/env bash
# Parte do setup do Dell all-in-one que precisa de root (o resto roda como usuario).
# O Ubuntu Server veio sem desktop, sem audio de usuario e sem libGL; o app do
# robo precisa de uma tela (X11), mic/alto-falante e do OpenCV com janela.
#
#   sudo bash scripts/dell/instalar-sistema.sh
set -eu
USUARIO="${SUDO_USER:-lsa-robot}"

apt-get update
apt-get install -y \
    xorg openbox lightdm lightdm-gtk-greeter x11-xserver-utils xdotool \
    pipewire pipewire-pulse wireplumber pulseaudio-utils alsa-utils \
    libgl1 libglib2.0-0t64 libfontconfig1 libdbus-1-3 libxkbcommon-x11-0 \
    libxcb-icccm4 libxcb-image0 libxcb-keysyms1 libxcb-render-util0 \
    libxcb-xinerama0 libxcb-xkb1 libxcb-shape0 libxcb-randr0 libxcb-cursor0 \
    v4l-utils

# PrimeSense (1d27:0601): libera o USB pro usuario (sem isso o OpenNI2 diz "no devices found")
cat > /etc/udev/rules.d/55-primesense.rules <<'RULE'
SUBSYSTEM=="usb", ATTRS{idVendor}=="1d27", MODE="0666"
RULE
udevadm control --reload-rules && udevadm trigger --subsystem-match=usb

# acesso a camera/audio e servicos de usuario sem precisar de login grafico
usermod -aG video,audio "$USUARIO"
loginctl enable-linger "$USUARIO"

# entra direto no desktop (robo e' um quiosque) e abre o app no login
mkdir -p /etc/lightdm/lightdm.conf.d
cat > /etc/lightdm/lightdm.conf.d/50-robo.conf <<CONF
[Seat:*]
autologin-user=$USUARIO
autologin-session=openbox
CONF
systemctl set-default graphical.target

echo
echo "Pronto. Reinicie (sudo reboot) para entrar no desktop."
