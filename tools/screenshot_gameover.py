"""Renderiza la pantalla de fin de partida (victoria y derrota) a PNG.

Uso:
    python tools/screenshot_gameover.py
"""

import os

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

import pygame

from wom.core.game import Game, Player
from wom.core.mapgen import MapParams
from wom.core.victory import VictoryMode, VictoryResult
from wom.ui import theme
from wom.ui.game_screen import GameScreen


def render(winner: int, label: str) -> None:
    screen = pygame.display.set_mode(theme.WINDOW_SIZE)
    players = [Player(0, "Jugador"), Player(1, "AI (medio)", is_ai=True)]
    game = Game.new(MapParams(seed=7), players, VictoryMode.TOTAL)
    game.turn = 23
    game.battles_fought = 14
    game.players[0].troops_lost = 87
    game.players[1].troops_lost = 142
    gs = GameScreen(game, human_id=0, ai_level="medio")
    result = VictoryResult(
        is_over=True, winner=winner, mode=VictoryMode.TOTAL,
        reason="el rival no tiene ejércitos ni fuertes",
    )
    gs.result = result
    gs.draw(screen)
    out = os.path.join(
        os.path.dirname(__file__), "..", "docs", f"screenshot_gameover_{label}.png"
    )
    pygame.image.save(screen, os.path.abspath(out))
    print(f"screenshot_gameover_{label}.png ok")


def main() -> None:
    pygame.init()
    render(winner=1, label="derrota")
    render(winner=0, label="victoria")


if __name__ == "__main__":
    main()
