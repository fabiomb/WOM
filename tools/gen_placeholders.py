"""Genera los PNG placeholder en data/assets/ para el arte final.

Convención de tamaños (el arte final reemplaza archivos con el mismo
nombre y dimensiones):
- Tiles de terreno: 64x64 px  (plains, forest, mountain, water)
- Unidades/ejércitos: 48x48 px (una por clase: partisano, soldado,
  caballero, arquero) — color de fondo según jugador se aplica en runtime.
- Íconos: 32x32 px (fort, town, bandera, cruz de ejército muerto)

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
    "flag": (220, 60, 60),
    "cross": (40, 40, 40),
}


def _make(name: str, size: int, color: tuple[int, int, int], out_dir: Path) -> None:
    surface = pygame.Surface((size, size))
    surface.fill(color)
    pygame.draw.rect(surface, (0, 0, 0), surface.get_rect(), 2)
    font = pygame.font.SysFont(None, size // 2)
    letter = font.render(name[0].upper(), True, (255, 255, 255))
    surface.blit(letter, letter.get_rect(center=surface.get_rect().center))
    pygame.image.save(surface, str(out_dir / f"{name}.png"))


def main() -> None:
    pygame.init()
    for group, size in ((TILES, 64), (UNITS, 48), (ICONS, 32)):
        for name, color in group.items():
            ASSETS_DIR.mkdir(parents=True, exist_ok=True)
            _make(name, size, color, ASSETS_DIR)
    print(f"Placeholders generados en {ASSETS_DIR}")
    pygame.quit()


if __name__ == "__main__":
    main()
