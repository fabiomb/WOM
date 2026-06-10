"""Smoke tests de la UI con el driver dummy de SDL (sin abrir ventana).

Simulan una partida humano vs AI: render, selección por click, trazado de
path, fin de turno (M2) y el menú principal + guardar/cargar (M4).
"""

import os

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

import pygame
import pytest

import wom.persistence.savegame as savegame
from wom.core.game import Game, Player
from wom.core.mapgen import MapParams
from wom.core.victory import VictoryMode
from wom.ui import theme
from wom.ui.game_screen import GameScreen
from wom.ui.menu_screen import LoadChoice, MenuScreen, NewGameChoice


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


def test_animacion_de_movimiento(screen, game_screen):
    game = game_screen.game
    army = game.armies_of(0)[0]
    start = army.position
    x, y = start
    target = (x + 2, y) if game.world.is_passable((x + 2, y)) else (x, y + 2)
    _click(game_screen, game_screen.renderer.tile_center(start))
    _click(game_screen, game_screen.renderer.tile_center(target))
    game_screen.end_turn()

    assert game_screen.animation is not None
    assert game_screen.animating  # recién terminado el turno: animando
    game_screen.draw(screen)  # dibuja posiciones interpoladas sin romper

    # durante la animación el input normal queda bloqueado...
    game_screen.handle_event(
        pygame.event.Event(pygame.KEYDOWN, {"key": pygame.K_g})
    )
    assert game_screen.notice is None  # la G no guardó: estaba animando
    # ...y Enter la saltea
    game_screen.handle_event(
        pygame.event.Event(pygame.KEYDOWN, {"key": pygame.K_RETURN})
    )
    assert not game_screen.animating
    game_screen.draw(screen)  # vuelve el dibujo normal y descarta la animación
    assert game_screen.animation is None


def test_fusion_por_shift_click(screen, game_screen):
    game = game_screen.game
    a = game.armies_of(0)[0]
    adjacent = next(
        pos for pos in game.world.neighbors(a.position) if game.army_at(pos) is None
    )
    b = game.spawn_army(0, adjacent, {"soldado": 10})
    troops_before = a.total_troops + b.total_troops

    _click(game_screen, game_screen.renderer.tile_center(a.position))  # selecciona a
    pygame.key.set_mods(pygame.KMOD_SHIFT)
    try:
        _click(game_screen, game_screen.renderer.tile_center(b.position))
    finally:
        pygame.key.set_mods(0)
    assert game_screen.pending_merge == (a.id, b.id)
    game_screen.draw(screen)  # el diálogo modal no debe romper el dibujo

    # con el diálogo abierto, ESC cancela (no vuelve al menú)
    game_screen.handle_event(pygame.event.Event(pygame.KEYDOWN, {"key": pygame.K_ESCAPE}))
    assert game_screen.pending_merge is None and not game_screen.wants_menu

    # de nuevo, ahora confirmando con la tecla S
    pygame.key.set_mods(pygame.KMOD_SHIFT)
    try:
        _click(game_screen, game_screen.renderer.tile_center(b.position))
    finally:
        pygame.key.set_mods(0)
    game_screen.handle_event(pygame.event.Event(pygame.KEYDOWN, {"key": pygame.K_s}))
    assert game.army_by_id(a.id) is None  # a se integró en b
    merged = game.army_by_id(b.id)
    assert merged.total_troops == troops_before
    assert game_screen.selected_id == b.id
    assert "fusionados" in game_screen.notice
    game_screen.draw(screen)


def test_guardar_desde_la_partida(screen, game_screen, tmp_path, monkeypatch):
    monkeypatch.setattr(savegame, "SAVES_DIR", tmp_path)
    game_screen.handle_event(pygame.event.Event(pygame.KEYDOWN, {"key": pygame.K_g}))
    saved = list(tmp_path.glob("*.json"))
    assert len(saved) == 1
    assert game_screen.notice is not None and "guardada" in game_screen.notice
    game_screen.draw(screen)  # el aviso no debe romper el HUD
    loaded = savegame.load_game(saved[0])
    assert loaded.to_dict() == game_screen.game.to_dict()


def test_esc_pide_confirmacion_antes_de_salir(screen, game_screen):
    esc = pygame.event.Event(pygame.KEYDOWN, {"key": pygame.K_ESCAPE})
    game_screen.handle_event(esc)
    assert game_screen.pending_quit and not game_screen.wants_menu
    game_screen.draw(screen)  # el diálogo no debe romper el dibujo

    # N (o ESC de nuevo) cancela y la partida sigue
    game_screen.handle_event(pygame.event.Event(pygame.KEYDOWN, {"key": pygame.K_n}))
    assert not game_screen.pending_quit and not game_screen.wants_menu

    # ESC y confirmar con S: ahora sí vuelve al menú
    game_screen.handle_event(esc)
    game_screen.handle_event(pygame.event.Event(pygame.KEYDOWN, {"key": pygame.K_s}))
    assert game_screen.wants_menu


def test_menu_nueva_partida(screen):
    menu = MenuScreen(default_ai_level="facil")
    menu.draw(screen)
    _click_menu(menu, "new")
    assert menu.mode == "new"
    menu.draw(screen)

    _click_menu(menu, "ai_level")  # facil → medio
    _click_menu(menu, "victory")   # total → flags
    menu.draw(screen)
    _click_menu(menu, "start")
    action = menu.take_action()
    assert isinstance(action, NewGameChoice)
    assert action.ai_level == "medio"
    assert action.victory_mode == VictoryMode.FLAGS
    assert action.map_params().width == 30  # tamaño "medio" por defecto
    assert menu.take_action() is None  # la acción se consume


def test_menu_cargar_partida(screen, tmp_path, monkeypatch):
    monkeypatch.setattr(savegame, "SAVES_DIR", tmp_path)
    players = [Player(0, "Humano"), Player(1, "AI", is_ai=True, ai_level="medio")]
    game = Game.new(MapParams(seed=11), players, VictoryMode.TOTAL)
    path = savegame.save_game(game)

    menu = MenuScreen()
    menu.draw(screen)
    _click_menu(menu, "load")
    assert menu.mode == "load"
    menu.draw(screen)
    _click_menu(menu, f"save:{path}")
    action = menu.take_action()
    assert isinstance(action, LoadChoice) and action.path == path


def test_menu_opciones(screen, tmp_path):
    from wom.persistence.settings import Settings, load_settings
    from wom.ui.music import MusicPlayer

    player = MusicPlayer(
        settings=Settings(music_enabled=False, music_folder=str(tmp_path)),
        settings_path=tmp_path / "settings.json",
    )
    menu = MenuScreen(music=player)
    menu.draw(screen)
    _click_menu(menu, "options")
    assert menu.mode == "options"
    menu.draw(screen)

    _click_menu(menu, "music_volume")  # 70% → 80%
    assert player.settings.music_volume == 0.8
    _click_menu(menu, "music_order")  # aleatorio → secuencial
    assert player.settings.music_shuffle is False
    menu.draw(screen)

    # editar la carpeta: click, borrar un caracter, escribir otro, Enter
    _click_menu(menu, "music_folder")
    for key, unicode in (
        (pygame.K_BACKSPACE, "\b"), (pygame.K_a, "a"), (pygame.K_RETURN, "\r"),
    ):
        menu.handle_event(
            pygame.event.Event(pygame.KEYDOWN, {"key": key, "unicode": unicode})
        )
    assert player.settings.music_folder == str(tmp_path)[:-1] + "a"

    # opciones de video: la resolución cicla y el maximizado conmuta
    menu.draw(screen)
    _click_menu(menu, "video_res")  # 1280x720 → 1600x900
    assert player.settings.video_resolution == "1600x900"
    _click_menu(menu, "video_max")
    assert player.settings.video_maximized is True
    menu.draw(screen)

    saved = load_settings(tmp_path / "settings.json")  # todo quedó persistido
    assert saved.music_volume == 0.8 and saved.music_shuffle is False
    assert saved.video_resolution == "1600x900" and saved.video_maximized is True


def test_reproductor_modal_con_m(screen, tmp_path):
    from wom.persistence.settings import Settings
    from wom.ui.music import MusicPlayer
    from wom.ui.music_overlay import MusicOverlay

    player = MusicPlayer(
        settings=Settings(music_folder=str(tmp_path)),
        settings_path=tmp_path / "s.json",
    )
    overlay = MusicOverlay(player)
    key_m = pygame.event.Event(pygame.KEYDOWN, {"key": pygame.K_m})
    assert overlay.handle_event(key_m) and overlay.visible
    overlay.draw(screen)  # carpeta vacía: dibuja el aviso sin romper

    # mientras está abierto es modal: consume teclado y mouse
    assert overlay.handle_event(pygame.event.Event(pygame.KEYDOWN, {"key": pygame.K_SPACE}))
    click = pygame.event.Event(pygame.MOUSEBUTTONDOWN, {"pos": (0, 0), "button": 1})
    assert overlay.handle_event(click)

    overlay.handle_event(pygame.event.Event(pygame.KEYDOWN, {"key": pygame.K_ESCAPE}))
    assert not overlay.visible
    assert not overlay.handle_event(click)  # cerrado: deja pasar el input


def test_menu_esc_retrocede_y_sale(screen):
    menu = MenuScreen()
    menu.draw(screen)
    _click_menu(menu, "new")
    esc = pygame.event.Event(pygame.KEYDOWN, {"key": pygame.K_ESCAPE})
    menu.handle_event(esc)
    assert menu.mode == "main" and menu.take_action() is None
    menu.handle_event(esc)
    assert menu.take_action() == "quit"


def _click_menu(menu: MenuScreen, button_id: str) -> None:
    """Click en el centro de un botón del menú (requiere draw previo)."""
    point = menu._buttons[button_id].center
    menu.handle_event(
        pygame.event.Event(pygame.MOUSEBUTTONDOWN, {"pos": point, "button": 1})
    )


def test_partida_completa_desde_la_ui(screen, game_screen):
    limit = game_screen.game.config["turnos_limite_default"]
    for _ in range(limit + 1):
        game_screen.end_turn()
        game_screen.draw(screen)
        if game_screen.game_over:
            break
    assert game_screen.game_over  # modo TIME: termina a más tardar en el límite
    game_screen.draw(screen)  # overlay de fin de partida no debe romper
