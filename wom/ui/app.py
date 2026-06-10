"""Aplicación pygame: loop principal.

v1/M2: arranca directo en una partida humano vs AI con parámetros default.
El menú (nueva partida con parámetros, cargar, salir) llega en M4.
"""

from __future__ import annotations

import pygame

from wom.core.game import Game, Player
from wom.core.mapgen import MapParams
from wom.core.victory import VictoryMode
from wom.ui import theme
from wom.ui.game_screen import GameScreen

WINDOW_TITLE = "WOM"


def run(seed: int | None = None, ai_level: str = "facil") -> None:
    """Punto de entrada de la aplicación gráfica."""
    pygame.init()
    screen = pygame.display.set_mode(theme.WINDOW_SIZE)
    pygame.display.set_caption(WINDOW_TITLE)
    clock = pygame.time.Clock()

    players = [
        Player(0, "Jugador"),
        Player(1, f"AI ({ai_level})", is_ai=True),
    ]
    game = Game.new(MapParams(seed=seed), players, VictoryMode.TOTAL)
    game_screen = GameScreen(game, human_id=0, ai_level=ai_level)

    running = True
    while running:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
            elif event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
                running = False
            else:
                game_screen.handle_event(event)
        game_screen.draw(screen)
        pygame.display.flip()
        clock.tick(theme.FPS)

    pygame.quit()
