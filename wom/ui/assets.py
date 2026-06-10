"""Carga y escalado de los PNG de data/assets/.

Si falta un archivo se genera un cuadrado de color como fallback, así la UI
nunca rompe por un asset ausente (correr tools/gen_placeholders.py los crea).
"""

from __future__ import annotations

from pathlib import Path

import pygame

from wom.core.worldmap import Terrain

ASSETS_DIR = Path(__file__).resolve().parents[2] / "data" / "assets"

UNIT_IDS = ("partisano", "soldado", "caballero", "arquero")
ICON_IDS = ("fort", "town", "flag", "cross")
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
