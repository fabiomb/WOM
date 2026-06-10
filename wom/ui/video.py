"""Settings de video: tamaño de la ventana y arranque maximizado.

El juego renderiza siempre a la resolución lógica (theme.WINDOW_SIZE) y
pygame la escala a la ventana manteniendo la proporción (flag SCALED). La
"resolución" de las opciones es el tamaño físico de la ventana; maximizado
manda sobre ella.
"""

from __future__ import annotations

import os

import pygame

from wom.persistence.settings import Settings

RESOLUTIONS = ["1280x720", "1600x900", "1920x1080", "2560x1440"]
DEFAULT_RESOLUTION = (1280, 720)


def parse_resolution(text: str) -> tuple[int, int]:
    """'1920x1080' → (1920, 1080); ante cualquier problema, el default."""
    try:
        width, height = text.lower().split("x")
        return int(width), int(height)
    except ValueError:
        return DEFAULT_RESOLUTION


def apply_video_settings(settings: Settings) -> None:
    """Aplica tamaño/maximizado a la ventana actual.

    No-op silencioso si no hay ventana o el driver no lo soporta (headless).
    """
    if os.environ.get("SDL_VIDEODRIVER") == "dummy":
        # Redimensionar el display dummy invalida su surface (crash nativo
        # al dibujar después); headless no hay ventana real que ajustar.
        return
    try:
        window = pygame.Window.from_display_module()
        if settings.video_maximized:
            window.maximize()
        else:
            window.restore()
            window.size = parse_resolution(settings.video_resolution)
    except pygame.error:
        pass
