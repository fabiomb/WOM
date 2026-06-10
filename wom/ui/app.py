"""Aplicación pygame: loop principal y cambio de pantallas (menú ↔ partida).

El menú decide qué partida arrancar (nueva con parámetros o cargada de un
savegame); la partida vuelve al menú con ESC. Guardar está disponible
durante la partida (botón del HUD o tecla G).
"""

from __future__ import annotations

import pygame

from wom.core.game import Game, Player
from wom.core.victory import VictoryMode
from wom.persistence.savegame import load_game
from wom.ui import theme
from wom.ui.game_screen import GameScreen
from wom.ui.menu_screen import LoadChoice, MenuScreen, NewGameChoice

WINDOW_TITLE = "WOM"
HUMAN_ID = 0


def new_game(choice: NewGameChoice) -> Game:
    """Crea una partida humano vs AI según lo elegido en el menú."""
    players = [
        Player(HUMAN_ID, "Jugador"),
        Player(1, f"AI ({choice.ai_level})", is_ai=True, ai_level=choice.ai_level),
    ]
    return Game.new(choice.map_params(), players, choice.victory_mode)


def run(seed: int | None = None, ai_level: str = "medio") -> None:
    """Punto de entrada de la aplicación gráfica. Arranca en el menú."""
    pygame.init()
    screen = pygame.display.set_mode(theme.WINDOW_SIZE)
    pygame.display.set_caption(WINDOW_TITLE)
    clock = pygame.time.Clock()

    current: MenuScreen | GameScreen = MenuScreen(ai_level, seed)
    running = True
    while running:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
            else:
                current.handle_event(event)

        if isinstance(current, MenuScreen):
            action = current.take_action()
            if action == "quit":
                running = False
            elif isinstance(action, NewGameChoice):
                current = GameScreen(new_game(action), human_id=HUMAN_ID)
            elif isinstance(action, LoadChoice):
                current = GameScreen(load_game(action.path), human_id=HUMAN_ID)
        elif current.wants_menu:
            current = MenuScreen(ai_level, seed)

        current.draw(screen)
        pygame.display.flip()
        clock.tick(theme.FPS)

    pygame.quit()
