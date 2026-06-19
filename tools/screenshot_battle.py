"""Genera docs/screenshot_batalla.png: un frame del zoom de batalla.

Render del combate táctico en tiempo real (campo abierto) a mitad de pelea,
con el fondo de pradera, la profundidad de las tropas y flechas en vuelo. Usa
el driver dummy de SDL, así que no abre ventana.

Uso:
    python tools/screenshot_battle.py
"""

import os

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

import random

import pygame

from wom.core.army import Army
from wom.core.config import load_game_config, load_unit_classes
from wom.core.tactical import build_tactical_battle
from wom.core.worldmap import Terrain, WorldMap
from wom.ui import theme
from wom.ui.battle_screen import BattleScreen


def main() -> None:
    pygame.init()
    screen = pygame.display.set_mode(theme.WINDOW_SIZE)
    cfg = load_game_config()
    tcfg = cfg["batalla"]["tactico"]
    w, h = tcfg["campo_ancho"], tcfg["campo_alto"]
    tiles = [[Terrain.PLAINS] * w for _ in range(h)]
    world = WorldMap(width=w, height=h, tiles=tiles)
    attacker = Army(
        id=0, owner=0, position=(w // 5, h // 2),
        composition={"soldado": 40, "caballero": 15, "arquero": 20},
    )
    defender = Army(
        id=1, owner=1, position=(4 * w // 5, h // 2),
        composition={"soldado": 40, "arquero": 20, "partisano": 15},
    )
    battle = build_tactical_battle(
        attacker, defender, world, load_unit_classes(), cfg["batalla"], random.Random(7)
    )
    bs = BattleScreen(battle, human_owner=0, enemy_level="medio")
    bs.phase = "fighting"
    for _ in range(140):  # ~4.6 s de combate
        bs.ai.update(battle, 1 / 30)
        battle.step(1 / 30)
        bs._spawn_arrows()
        bs._age_arrows(1 / 30)
    # Un grupo propio seleccionado, para mostrar los anillos de selección.
    mine = sorted(
        (u for u in battle.units if u.owner == 0 and u.active), key=lambda u: u.x
    )
    bs.selected = {u.id for u in mine[: max(1, len(mine) // 2)]}

    bs.draw(screen)
    out = os.path.join(os.path.dirname(__file__), "..", "docs", "screenshot_batalla.png")
    pygame.image.save(screen, os.path.abspath(out))
    print("screenshot batalla ok")


if __name__ == "__main__":
    main()
