"""Smoke tests de la UI con el driver dummy de SDL (sin abrir ventana).

Simulan una partida humano vs AI: render, selección por click, trazado de
path y fin de turno — el flujo completo de M2.
"""

import os

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

import pygame
import pytest

from wom.core.game import Game, Player
from wom.core.mapgen import MapParams
from wom.core.victory import VictoryMode
from wom.ui import theme
from wom.ui.game_screen import GameScreen


@pytest.fixture(scope="module")
def screen():
    pygame.init()
    yield pygame.display.set_mode(theme.WINDOW_SIZE)
    pygame.quit()


@pytest.fixture()
def game_screen(screen) -> GameScreen:
    players = [Player(0, "Humano"), Player(1, "AI", is_ai=True)]
    game = Game.new(MapParams(seed=2026), players, VictoryMode.TIME)
    return GameScreen(game, human_id=0, ai_level="facil")


def _click(gs: GameScreen, point, button=1):
    gs.handle_event(
        pygame.event.Event(pygame.MOUSEBUTTONDOWN, {"pos": point, "button": button})
    )


def test_render_inicial(screen, game_screen):
    game_screen.draw(screen)  # no debe lanzar


def test_seleccion_y_path_por_clicks(screen, game_screen):
    game = game_screen.game
    army = game.armies_of(0)[0]
    _click(game_screen, game_screen.renderer.tile_center(army.position))
    assert game_screen.selected_id == army.id

    x, y = army.position
    target = (x + 3, y) if game.world.is_passable((x + 3, y)) else (x, y + 3)
    _click(game_screen, game_screen.renderer.tile_center(target))
    path = game_screen.pending_paths[army.id]
    assert path and path[-1] == target

    # click derecho: borra el path y deselecciona
    _click(game_screen, (0, 0), button=3)
    assert game_screen.selected_id is None
    assert army.id not in game_screen.pending_paths


def test_fin_de_turno_ejecuta_el_juego(screen, game_screen):
    game = game_screen.game
    army = game.armies_of(0)[0]
    start = army.position
    x, y = start
    target = (x + 2, y) if game.world.is_passable((x + 2, y)) else (x, y + 2)
    _click(game_screen, game_screen.renderer.tile_center(start))
    _click(game_screen, game_screen.renderer.tile_center(target))

    game_screen.handle_event(
        pygame.event.Event(pygame.KEYDOWN, {"key": pygame.K_RETURN})
    )
    assert game.turn == 1
    assert army.position != start  # la orden del humano se ejecutó
    game_screen.draw(screen)


def test_boton_fin_de_turno(screen, game_screen):
    _click(game_screen, game_screen.hud.button.center)
    assert game_screen.game.turn == 1


def test_crear_ejercito_desde_fuerte(screen, game_screen):
    game = game_screen.game
    fort = next(f for f in game.world.forts if f.owner == 0)
    fort.reserve = {"soldado": 50}
    # correr el ejército inicial del fuerte a un tile libre cualquiera
    army = game.armies_of(0)[0]
    army.position = next(
        (x, y)
        for y in range(game.world.height)
        for x in range(game.world.width)
        if game.world.is_passable((x, y))
        and game.army_at((x, y)) is None
        and game.world.fort_at((x, y)) is None
    )

    _click(game_screen, game_screen.renderer.tile_center(fort.position))
    assert game_screen.selected_fort == fort.position
    game_screen.draw(screen)  # el draw habilita el botón "Crear ejército"

    _click(game_screen, game_screen.hud.create_button.center)
    assert fort.position in game_screen.pending_creations

    game_screen.end_turn()
    created = game.army_at(fort.position)
    assert created is not None and created.owner == 0
    assert created.total_troops >= 50


def test_partida_completa_desde_la_ui(screen, game_screen):
    limit = game_screen.game.config["turnos_limite_default"]
    for _ in range(limit + 1):
        game_screen.end_turn()
        game_screen.draw(screen)
        if game_screen.game_over:
            break
    assert game_screen.game_over  # modo TIME: termina a más tardar en el límite
    game_screen.draw(screen)  # overlay de fin de partida no debe romper
