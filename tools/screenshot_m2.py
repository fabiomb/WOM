"""Genera docs/screenshot_m2.png: un frame real de la pantalla de juego.

Usa el driver dummy de SDL, así que no abre ventana. Útil para verificar
el render sin lanzar el juego.

Uso:
    python tools/screenshot_m2.py
"""

import os

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

import pygame

from wom.core.game import Game, Player
from wom.core.mapgen import MapParams
from wom.core.victory import VictoryMode
from wom.ui import theme
from wom.ui.game_screen import GameScreen


def main() -> None:
    pygame.init()
    screen = pygame.display.set_mode(theme.WINDOW_SIZE)
    players = [Player(0, "Jugador"), Player(1, "AI (facil)", is_ai=True)]
    game = Game.new(MapParams(seed=42), players, VictoryMode.TOTAL)
    gs = GameScreen(game, human_id=0, ai_level="facil")

    # Trazar un camino al primer ejército para ver el path dibujado.
    army = game.armies_of(0)[0]
    gs._left_click(gs.renderer.tile_center(army.position))
    x, y = army.position
    gs._left_click(gs.renderer.tile_center((x + 6, y + 3)))

    # Correr unos turnos para que haya movimiento, batallas y producción.
    for _ in range(3):
        gs.end_turn()

    # Seleccionar el fuerte propio con reserva para mostrar su panel y botón.
    fort = next(f for f in game.world.forts if f.owner == 0)
    fort.reserve = {"soldado": 25, "arquero": 12, "caballero": 8}
    if game.army_at(fort.position) is None:
        gs._left_click(gs.renderer.tile_center(fort.position))

    gs.draw(screen)
    out = os.path.join(os.path.dirname(__file__), "..", "docs", "screenshot_m2.png")
    pygame.image.save(screen, os.path.abspath(out))
    print(f"screenshot ok, turno {game.turn}")


if __name__ == "__main__":
    main()
