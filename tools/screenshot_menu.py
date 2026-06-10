"""Renderiza las tres vistas del menú a docs/screenshot_menu_*.png (sin ventana).

Uso: PYTHONPATH=raíz del proyecto, luego `python tools/screenshot_menu.py`.
"""

import os
import sys
from pathlib import Path

os.environ["SDL_VIDEODRIVER"] = "dummy"

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pygame

from wom.ui import theme
from wom.ui.menu_screen import MenuScreen


def main() -> None:
    pygame.init()
    screen = pygame.display.set_mode(theme.WINDOW_SIZE)
    docs = Path(__file__).resolve().parents[1] / "docs"
    menu = MenuScreen()
    for mode in ("main", "new", "load"):
        menu.mode = mode
        menu.draw(screen)
        out = docs / f"screenshot_menu_{mode}.png"
        pygame.image.save(screen, str(out))
        print(f"guardado {out}")
    pygame.quit()


if __name__ == "__main__":
    main()
