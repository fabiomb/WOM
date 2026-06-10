"""Aplicación pygame: loop principal y cambio de pantallas (menú ↔ partida).

El menú decide qué partida arrancar (nueva con parámetros o cargada de un
savegame); la partida vuelve al menú con ESC. Guardar está disponible
durante la partida (botón del HUD o tecla G). La música arranca con la app
(un tema al azar de la carpeta configurada) y la tecla M abre el reproductor
modal por encima de cualquier pantalla.
"""

from __future__ import annotations

import pygame

from wom.core.game import Game, Player
from wom.core.victory import VictoryMode
from wom.persistence.savegame import load_game
from wom.ui import theme
from wom.ui.game_screen import GameScreen
from wom.ui.menu_screen import LoadChoice, MenuScreen, NewGameChoice
from wom.ui.music import MusicPlayer
from wom.ui.music_overlay import MusicOverlay
from wom.ui.video import apply_video_settings

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
    # RESIZABLE + SCALED: la ventana se puede maximizar o redimensionar y
    # pygame escala la resolución lógica (WINDOW_SIZE) manteniendo la
    # proporción (bandas negras si hace falta); el mouse ya llega traducido.
    screen = pygame.display.set_mode(
        theme.WINDOW_SIZE, pygame.RESIZABLE | pygame.SCALED
    )
    pygame.display.set_caption(WINDOW_TITLE)
    clock = pygame.time.Clock()

    music = MusicPlayer()
    music.start()  # un tema al azar desde el arranque (si está habilitada)
    music_overlay = MusicOverlay(music)
    apply_video_settings(music.settings)  # resolución / maximizado guardados

    current: MenuScreen | GameScreen = MenuScreen(ai_level, seed, music=music)
    running = True
    while running:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
            elif music.handle_event(event):
                pass  # fin del tema: la playlist ya avanzó
            elif music_overlay.handle_event(event):
                pass  # M o input del reproductor modal
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
            current = MenuScreen(ai_level, seed, music=music)

        current.draw(screen)
        music_overlay.draw(screen)
        pygame.display.flip()
        clock.tick(theme.FPS)

    pygame.quit()
