"""
janela.py — utilitario de janela compartilhado entre os apps de camera
(experiments/camera_prime_sense/Camera_Simples.py e jetson/kinect_camera.py).

Sem "from __future__ import annotations": jetson/kinect_camera.py roda no
Python 3.6 da Jetson, que nao aceita esse import.
"""

import os
import subprocess


def tamanho_tela_cheia():
    """Resolucao do monitor via xrandr; usa 1920x1080 se nao conseguir."""
    try:
        saida = subprocess.check_output(
            ["xrandr", "--current"], env=os.environ, timeout=3
        ).decode()
        for linha in saida.splitlines():
            if "*" in linha:
                modo = linha.split()[0]
                w, h = modo.split("x")
                return int(w), int(h)
    except Exception:
        pass
    return 1920, 1080
