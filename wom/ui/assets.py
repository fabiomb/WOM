"""Carga y escalado de los PNG de data/assets/.

Si falta un archivo se genera un cuadrado de color como fallback, así la UI
nunca rompe por un asset ausente (correr tools/gen_placeholders.py los crea).
"""

from __future__ import annotations

from pathlib import Path

import pygame

from wom.core.worldmap import Terrain
from wom.paths import resource_root
from wom.ui.tiling import WATER_CORNER_VARIANTS, WATER_VARIANTS

ASSETS_DIR = resource_root() / "data" / "assets"

UNIT_IDS = ("partisano", "soldado", "caballero", "arquero")
ICON_IDS = (
    "fort", "town", "flag", "flag_red", "flag_blue", "flag_green", "flag_yellow", "cross"
)
FALLBACK_COLOR = (200, 0, 200)


class Assets:
    """Sprites escalados al tamaño de tile de la partida en pantalla."""

    def __init__(self, tile_size: int):
        self.tile_size = tile_size
        unit_size = max(8, int(tile_size * 0.72))
        icon_size = max(8, int(tile_size * 0.8))
        self.terrain = {
            t: _load(t.value, tile_size) for t in Terrain
        }
        # Variantes de costa del agua (autotiling, ver wom/ui/tiling.py).
        self.water = {name: _load(name, tile_size) for name in WATER_VARIANTS}
        # Overlays de esquina (transparentes): suavizan las puntas de tierra
        # en diagonal que el autotiling ortogonal no cubre.
        self.water_corners = {
            name: _load(name, tile_size) for name in WATER_CORNER_VARIANTS
        }
        self.units = {u: _load(u, unit_size) for u in UNIT_IDS}
        self.icons = {i: _load(i, icon_size) for i in ICON_IDS}


def _load(name: str, size: int) -> pygame.Surface:
    path = ASSETS_DIR / f"{name}.png"
    if path.exists():
        image = pygame.image.load(str(path))
        if pygame.display.get_surface() is not None:
            image = image.convert_alpha()
        return pygame.transform.scale(image, (size, size))
    surface = pygame.Surface((size, size))
    surface.fill(FALLBACK_COLOR)
    return surface


def load_scaled(name: str, size: int) -> pygame.Surface:
    """Carga un sprite de data/assets/ escalado a `size`×`size`.

    Igual que el escalado interno de `Assets`, pero a un tamaño arbitrario:
    útil fuera del mapa (p. ej. la ayuda de F1, que muestra los mismos tiles).
    Si el archivo falta devuelve un cuadrado de color (fallback).
    """
    return _load(name, size)


def load_image(name: str) -> pygame.Surface | None:
    """Carga un PNG de data/assets/ a su tamaño original, o None si falta.

    Útil para imágenes que no son sprites de tile (p. ej. la ilustración de
    victoria/derrota de la pantalla de fin de partida).
    """
    path = ASSETS_DIR / f"{name}.png"
    if not path.exists():
        return None
    image = pygame.image.load(str(path))
    if pygame.display.get_surface() is not None:
        image = image.convert_alpha()
    return image
