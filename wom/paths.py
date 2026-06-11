"""Rutas del proyecto: recursos empaquetados vs. archivos del usuario.

En desarrollo todo cuelga de la raíz del repo. Congelado con PyInstaller
(`sys.frozen`), los recursos de solo lectura (data/) viven dentro del bundle
(`sys._MEIPASS`) y lo escribible (saves/, settings.json) va junto al
ejecutable, estilo aplicación portable.
"""

from __future__ import annotations

import sys
from pathlib import Path


def resource_root() -> Path:
    """Raíz de los recursos de solo lectura (data/config, data/assets...)."""
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS)
    return Path(__file__).resolve().parents[1]


def user_root() -> Path:
    """Raíz de lo que el juego escribe (saves/, settings.json)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]
