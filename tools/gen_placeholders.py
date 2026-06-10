"""Genera los PNG placeholder en data/assets/ para el arte final.

Convención de tamaños (el arte final reemplaza archivos con el mismo
nombre y dimensiones):
- Tiles de terreno: 64x64 px  (plains, forest, mountain, water)
- Unidades/ejércitos: 48x48 px (una por clase: partisano, soldado,
  caballero, arquero) — color de fondo según jugador se aplica en runtime.
- Íconos: 32x32 px (fort, town, cruz de ejército muerto, y las banderas:
  flag_red para el jugador 0, flag_blue para el 1, flag gris para neutrales)

Uso:
    python tools/gen_placeholders.py
"""

from __future__ import annotations

from pathlib import Path

import pygame

ASSETS_DIR = Path(__file__).resolve().parents[1] / "data" / "assets"

TILES = {  # 64x64, color plano + letra
    "plains": (110, 160, 70),
    "forest": (40, 100, 45),
    "mountain": (130, 120, 110),
    "water": (50, 90, 160),
}
UNITS = {  # 48x48
    "partisano": (200, 170, 60),
    "soldado": (170, 170, 180),
    "caballero": (190, 120, 60),
    "arquero": (100, 170, 130),
}
ICONS = {  # 32x32
    "fort": (90, 80, 70),
    "town": (180, 150, 100),
    "cross": (40, 40, 40),
}
FLAGS = {  # 32x32, mástil + paño del color del dueño (ver theme.PLAYER_COLORS)
    "flag": (150, 150, 150),      # sitio neutral
    "flag_red": (210, 70, 60),    # jugador 0 (humano)
    "flag_blue": (70, 110, 210),  # jugador 1 (AI)
}


def _make(name: str, size: int, color: tuple[int, int, int], out_dir: Path) -> None:
    surface = pygame.Surface((size, size))
    surface.fill(color)
    pygame.draw.rect(surface, (0, 0, 0), surface.get_rect(), 2)
    font = pygame.font.SysFont(None, size // 2)
    letter = font.render(name[0].upper(), True, (255, 255, 255))
    surface.blit(letter, letter.get_rect(center=surface.get_rect().center))
    pygame.image.save(surface, str(out_dir / f"{name}.png"))


def _make_flag(name: str, size: int, color: tuple[int, int, int], out_dir: Path) -> None:
    """Bandera con fondo transparente: mástil y paño del color del dueño."""
    surface = pygame.Surface((size, size), pygame.SRCALPHA)
    pole_x = size // 4
    pygame.draw.line(surface, (70, 55, 40), (pole_x, size // 8),
                     (pole_x, size - size // 8), max(2, size // 12))
    pennant = [
        (pole_x + 1, size // 8),
        (size - size // 8, size // 4 + size // 16),
        (pole_x + 1, size // 2),
    ]
    pygame.draw.polygon(surface, color, pennant)
    pygame.draw.polygon(surface, (0, 0, 0), pennant, 1)
    pygame.image.save(surface, str(out_dir / f"{name}.png"))


def main() -> None:
    pygame.init()
    ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    for group, size in ((TILES, 64), (UNITS, 48), (ICONS, 32)):
        for name, color in group.items():
            _make(name, size, color, ASSETS_DIR)
    for name, color in FLAGS.items():
        _make_flag(name, 32, color, ASSETS_DIR)
    print(f"Placeholders generados en {ASSETS_DIR}")
    pygame.quit()


if __name__ == "__main__":
    main()
