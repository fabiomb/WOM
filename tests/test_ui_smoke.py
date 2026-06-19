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


def _auto_resolve_battles(gs: GameScreen):
    """El zoom de batalla pregunta por cada combate del humano; elegir siempre
    'auto-resolver' reproduce el camino determinista de antes (resolve_battle).
    Cierra el turno cuando se vacía la cola de batallas."""
    while gs._tactical_prompt is not None:
        gs._resolve_tactical_prompt(zoom=False)


def test_render_inicial(screen, game_screen):
    game_screen.draw(screen)  # no debe lanzar


def test_resaltado_del_ejercito_inicial(screen, game_screen):
    army = game_screen.game.armies_of(0)[0]
    assert army.position in game_screen.spawn_highlights  # partida nueva
    game_screen.draw(screen)  # dibuja los anillos sin romper
    _click(game_screen, game_screen.renderer.tile_center(army.position))
    assert game_screen.spawn_highlights == []  # encontrado: se apaga


def test_animacion_de_choque_en_batalla(screen, game_screen):
    game = game_screen.game
    a = game.armies_of(0)[0]
    enemy_pos = next(
        pos for pos in game.world.neighbors(a.position)
        if game.army_at(pos) is None and game.world.fort_at(pos) is None
    )
    b = game.spawn_army(1, enemy_pos, {"soldado": 100})
    game_screen.pending_paths[a.id] = [enemy_pos]
    game_screen.end_turn()
    _auto_resolve_battles(game_screen)  # el humano pelea: elegir auto-resolver
    assert game.last_clashes == [(a.id, b.id)]
    anim = game_screen.animation
    assert anim is not None and anim.clash_points

    # plantar el reloj en plena fase de choque y dibujar el destello
    in_clash = anim.move_duration + 0.3
    game_screen.animation_start = pygame.time.get_ticks() - int(in_clash * 1000)
    assert game_screen.animating
    assert anim.clash_effects(in_clash)
    game_screen.draw(screen)  # ejércitos embistiendo + destello sin romper


def test_zoom_de_batalla_aplica_resultado_y_cierra_turno(screen, game_screen):
    """Flujo completo del zoom: batalla del humano → prompt → dirigir → el
    BattleResult del combate se aplica y el turno se cierra."""
    game = game_screen.game
    a = game.armies_of(0)[0]
    enemy_pos = next(
        pos for pos in game.world.neighbors(a.position)
        if game.army_at(pos) is None and game.world.fort_at(pos) is None
    )
    b = game.spawn_army(1, enemy_pos, {"soldado": 30})
    game_screen.pending_paths[a.id] = [enemy_pos]
    turno = game.turn
    game_screen.end_turn()
    assert game_screen._tactical_prompt == (a.id, b.id)  # pregunta por la batalla
    game_screen._resolve_tactical_prompt(zoom=True)  # el jugador dirige
    bs = game_screen._tactical_battle
    assert bs is not None
    for _ in range(20000):  # corre el combate hasta el final
        bs.ai.update(bs.battle, 1 / 30)
        bs.battle.step(1 / 30)
        if bs.battle.finished:
            break
    assert bs.battle.finished
    bs.result = bs.battle.to_battle_result()
    game_screen._finish_tactical_battle()
    assert game_screen._tactical_battle is None
    assert game.turn == turno + 1            # el turno se cerró
    assert (a.id, b.id) in game.last_clashes  # la batalla quedó registrada
    assert game_screen.animation is not None  # recap del turno armado


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


def test_confirmar_ruta_con_click_o_esc(screen, game_screen):
    game = game_screen.game
    army = game.armies_of(0)[0]
    # correr el ejército fuera del fuerte para poder clickear el fuerte después
    army.position = next(
        (x, y)
        for y in range(game.world.height)
        for x in range(game.world.width)
        if game.world.is_passable((x, y))
        and game.army_at((x, y)) is None
        and game.world.fort_at((x, y)) is None
    )
    x, y = army.position
    target = (x + 2, y) if game.world.is_passable((x + 2, y)) else (x, y + 2)
    _click(game_screen, game_screen.renderer.tile_center(army.position))
    _click(game_screen, game_screen.renderer.tile_center(target))
    assert game_screen.pending_paths[army.id]

    # click en el mismo ejército: confirma la ruta y deselecciona
    _click(game_screen, game_screen.renderer.tile_center(army.position))
    assert game_screen.selected_id is None
    assert game_screen.pending_paths[army.id]  # la ruta trazada se conserva

    # ahora sí se puede clickear un fuerte propio (antes era imposible)
    fort = next(f for f in game.world.forts if f.owner == 0)
    _click(game_screen, game_screen.renderer.tile_center(fort.position))
    assert game_screen.selected_fort == fort.position

    # ESC con algo seleccionado deselecciona, sin abrir el diálogo de salida
    game_screen.handle_event(pygame.event.Event(pygame.KEYDOWN, {"key": pygame.K_ESCAPE}))
    assert game_screen.selected_fort is None and not game_screen.pending_quit


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

    _click_menu(menu, "ai_level:0")  # facil → medio (único rival por defecto)
    _click_menu(menu, "victory")   # total → flags
    menu.draw(screen)
    _click_menu(menu, "start")
    action = menu.take_action()
    assert isinstance(action, NewGameChoice)
    assert action.ai_levels == ("medio",)  # 1 rival
    assert action.victory_mode == VictoryMode.FLAGS
    assert action.map_params().width == 30  # tamaño "medio" por defecto
    assert menu.take_action() is None  # la acción se consume


def test_menu_cuatro_jugadores(screen):
    """Sumar rivales hasta 4 jugadores y elegir el nivel de cada IA."""
    menu = MenuScreen(default_ai_level="facil")
    menu.draw(screen)
    _click_menu(menu, "new")
    menu.draw(screen)
    _click_menu(menu, "n_opponents")  # 1 → 2 rivales (rival nuevo: "medio")
    _click_menu(menu, "n_opponents")  # 2 → 3 rivales (máximo: 4 jugadores)
    menu.draw(screen)
    _click_menu(menu, "ai_level:2")   # nivel del tercer rival: medio → dificil
    menu.draw(screen)
    _click_menu(menu, "start")
    action = menu.take_action()
    # El primer rival mantiene el nivel por defecto ("facil"); los agregados "medio".
    assert action.ai_levels == ("facil", "medio", "dificil")
    assert action.n_players == 4
    params = action.map_params()
    assert params.n_players == 4 and params.n_forts >= 4


def test_new_game_cuatro_jugadores_vs_ia(screen):
    """Flujo local completo: la decisión del menú arma 4 jugadores (humano + 3
    IAs con su nivel) y la partida corre un turno con las 3 IAs."""
    from wom.ui.app import new_game
    from wom.ui.menu_screen import NewGameChoice

    choice = NewGameChoice(("facil", "medio", "dificil"), "chico", VictoryMode.TOTAL, seed=5)
    game = new_game(choice)
    assert len(game.players) == 4
    assert game.players[0].is_ai is False
    assert [p.ai_level for p in game.players[1:]] == ["facil", "medio", "dificil"]
    assert len(game.armies_of(0)) == 1  # ejército inicial del humano

    gs = GameScreen(game, human_id=0)
    assert len(gs.ais) == 3
    gs.end_turn()
    assert game.turn == 1
    gs.draw(screen)


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
    assert menu.mode == "options"  # hub: Sonido / Video
    menu.draw(screen)

    _click_menu(menu, "sound")
    assert menu.mode == "sound"
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

    # ESC vuelve al hub; el submenú de video cicla resolución y maximizado
    menu.handle_event(pygame.event.Event(pygame.KEYDOWN, {"key": pygame.K_ESCAPE}))
    assert menu.mode == "options"
    menu.draw(screen)
    _click_menu(menu, "video")
    assert menu.mode == "video"
    menu.draw(screen)
    from wom.ui.video import RESOLUTIONS

    start = player.settings.video_resolution
    expected = RESOLUTIONS[(RESOLUTIONS.index(start) + 1) % len(RESOLUTIONS)]
    _click_menu(menu, "video_res")  # cicla a la resolución siguiente
    assert player.settings.video_resolution == expected
    _click_menu(menu, "video_max")
    assert player.settings.video_maximized is True
    menu.draw(screen)

    # ESC retrocede de a un nivel hasta el menú principal
    esc = pygame.event.Event(pygame.KEYDOWN, {"key": pygame.K_ESCAPE})
    menu.handle_event(esc)
    assert menu.mode == "options"
    menu.handle_event(esc)
    assert menu.mode == "main" and menu.take_action() is None

    saved = load_settings(tmp_path / "settings.json")  # todo quedó persistido
    assert saved.music_volume == 0.8 and saved.music_shuffle is False
    assert saved.video_resolution == expected and saved.video_maximized is True


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


def test_capturing_text_suspende_atajos_globales(screen, tmp_path):
    """Escribiendo en un campo de texto, las teclas no van al reproductor."""
    from wom.persistence.settings import Settings
    from wom.ui.app import _event_to_overlay
    from wom.ui.multiplayer_screen import MultiplayerScreen
    from wom.ui.music import MusicPlayer
    from wom.ui.music_overlay import MusicOverlay

    overlay = MusicOverlay(
        MusicPlayer(settings=Settings(music_folder=str(tmp_path)),
                    settings_path=tmp_path / "s.json")
    )
    key_m = pygame.event.Event(pygame.KEYDOWN, {"key": pygame.K_m})
    click = pygame.event.Event(pygame.MOUSEBUTTONDOWN, {"pos": (0, 0), "button": 1})

    mp = MultiplayerScreen()
    mp.mode = "connect"
    assert not mp.capturing_text
    assert _event_to_overlay(mp, overlay, key_m)  # sin foco: la M abre el player

    mp.focused = "ip"  # foco en un campo de texto
    assert mp.capturing_text
    assert not _event_to_overlay(mp, overlay, key_m)  # la M va al campo, no al player
    assert _event_to_overlay(mp, overlay, click)  # el mouse sigue el camino normal

    overlay.visible = True  # con el player abierto (modal) recupera el control
    assert _event_to_overlay(mp, overlay, key_m)


def test_capturing_text_de_cada_pantalla(screen):
    """Las pantallas con entrada de texto exponen capturing_text."""
    from wom.ui.multiplayer_screen import MultiplayerScreen

    menu = MenuScreen()
    assert not menu.capturing_text
    menu._editing_folder = "data/music"
    assert menu.capturing_text

    mp = MultiplayerScreen()
    assert not mp.capturing_text
    mp.focused = "name"
    assert mp.capturing_text

    players = [Player(0, "H"), Player(1, "AI", is_ai=True)]
    gs = GameScreen(Game.new(MapParams(seed=3), players, VictoryMode.TIME), human_id=0)
    assert not gs.capturing_text  # sin red no hay chat
    gs.chat_active = True
    assert gs.capturing_text


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


def test_zoom_con_la_rueda(screen, game_screen):
    renderer = game_screen.renderer
    base = renderer.tile_size
    game_screen.handle_event(pygame.event.Event(pygame.MOUSEWHEEL, {"x": 0, "y": 1}))
    assert renderer.tile_size > base
    # round-trip de coordenadas con el origen desplazado por el zoom
    assert renderer.screen_to_tile(renderer.tile_center((5, 5)), game_screen.game) == (5, 5)
    # un punto del HUD nunca mapea a un tile (aunque el mapa "siga" debajo)
    assert renderer.screen_to_tile(game_screen.hud.rect.center, game_screen.game) is None
    game_screen.draw(screen)  # dibuja recortado al área sin romper
    game_screen.handle_event(pygame.event.Event(pygame.MOUSEWHEEL, {"x": 0, "y": -1}))
    assert renderer.tile_size == base  # vuelve al encuadre completo


def test_grab_con_boton_del_medio(screen, game_screen):
    camera = game_screen.renderer.camera
    game_screen.handle_event(pygame.event.Event(pygame.MOUSEWHEEL, {"x": 0, "y": 1}))
    before = camera.origin
    game_screen.handle_event(pygame.event.Event(
        pygame.MOUSEBUTTONDOWN, {"pos": (700, 500), "button": 2}
    ))
    game_screen.handle_event(pygame.event.Event(
        pygame.MOUSEMOTION,
        {"pos": (640, 470), "rel": (-60, -30), "buttons": (0, 1, 0)},
    ))
    # El mapa sigue al mouse: el origen se corre lo mismo que el arrastre.
    assert camera.origin == (before[0] - 60, before[1] - 30)
    game_screen.handle_event(pygame.event.Event(
        pygame.MOUSEBUTTONUP, {"pos": (640, 470), "button": 2}
    ))
    moved = camera.origin
    game_screen.handle_event(pygame.event.Event(
        pygame.MOUSEMOTION,
        {"pos": (600, 400), "rel": (-40, -70), "buttons": (0, 0, 0)},
    ))
    assert camera.origin == moved  # soltado: mover el mouse ya no panea


def test_kp_enter_pasa_el_turno(screen, game_screen):
    game_screen.handle_event(
        pygame.event.Event(pygame.KEYDOWN, {"key": pygame.K_KP_ENTER})
    )
    assert game_screen.game.turn == 1


def test_doble_click_fija_la_ruta(screen, game_screen):
    game = game_screen.game
    army = game.armies_of(0)[0]
    x, y = army.position
    target = (x + 2, y) if game.world.is_passable((x + 2, y)) else (x, y + 2)
    _click(game_screen, game_screen.renderer.tile_center(army.position))
    _click(game_screen, game_screen.renderer.tile_center(target))
    assert game_screen.selected_id == army.id
    _click(game_screen, game_screen.renderer.tile_center(target))  # doble click
    assert game_screen.selected_id is None  # foco liberado...
    path = game_screen.pending_paths[army.id]
    assert path and path[-1] == target  # ...y la ruta quedó fijada


def test_dividir_con_modal(screen, game_screen):
    game = game_screen.game
    army = game.armies_of(0)[0]
    army.composition = {"soldado": 8, "arquero": 4}

    def _free(pos):
        return (
            game.world.is_passable(pos)
            and game.army_at(pos) is None
            and game.world.fort_at(pos) is None
            and game.world.town_at(pos) is None
        )

    # a campo abierto y con al menos un tile libre aledaño (destino del nuevo)
    army.position = next(
        (x, y)
        for y in range(game.world.height)
        for x in range(game.world.width)
        if _free((x, y)) and any(_free(n) for n in game.world.neighbors((x, y)))
    )
    _click(game_screen, game_screen.renderer.tile_center(army.position))
    game_screen.draw(screen)  # habilita el botón "Dividir ejército"
    _click(game_screen, game_screen.hud.split_button.center)
    assert game_screen.pending_split == army.id
    game_screen.draw(screen)  # dibuja el modal: habilita sus botones

    picker = game_screen.split_picker
    _click(game_screen, picker._buttons["plus:arquero"].center)
    _click(game_screen, picker._buttons["plus:arquero"].center)
    assert picker.amounts["arquero"] == 2
    game_screen.handle_event(pygame.event.Event(pygame.KEYDOWN, {"key": pygame.K_s}))
    assert game_screen.pending_split is None
    created = game.army_by_id(game_screen.selected_id)  # el nuevo queda elegido
    assert created is not None and created.id != army.id
    assert created.composition == {"arquero": 2}
    assert army.composition == {"soldado": 8, "arquero": 2}
    game_screen.draw(screen)


def test_fusion_parcial_por_clases(screen, game_screen):
    game = game_screen.game
    a = game.armies_of(0)[0]
    a.composition = {"soldado": 20}
    adjacent = next(
        pos for pos in game.world.neighbors(a.position) if game.army_at(pos) is None
    )
    b = game.spawn_army(0, adjacent, {"soldado": 10})

    _click(game_screen, game_screen.renderer.tile_center(a.position))
    pygame.key.set_mods(pygame.KMOD_SHIFT)
    try:
        _click(game_screen, game_screen.renderer.tile_center(b.position))
    finally:
        pygame.key.set_mods(0)
    assert game_screen.pending_merge == (a.id, b.id)
    picker = game_screen.merge_picker
    assert picker.amounts == {"soldado": 20}  # arranca en "Todo"
    game_screen.draw(screen)  # el modal no debe romper el dibujo

    picker.set_all(False)
    picker.adjust("soldado", 5)
    game_screen.handle_event(pygame.event.Event(pygame.KEYDOWN, {"key": pygame.K_s}))
    assert game.army_by_id(a.id) is not None  # transferencia parcial: sigue vivo
    assert a.composition == {"soldado": 15}
    assert b.composition == {"soldado": 15}
    assert "transferidas" in game_screen.notice.lower()


def test_partida_completa_desde_la_ui(screen, game_screen):
    limit = game_screen.game.config["turnos_limite_default"]
    for _ in range(limit + 1):
        game_screen.end_turn()
        _auto_resolve_battles(game_screen)  # responde el prompt de zoom si aparece
        game_screen.draw(screen)
        if game_screen.game_over:
            break
    assert game_screen.game_over  # modo TIME: termina a más tardar en el límite
    game_screen.draw(screen)  # overlay de fin de partida no debe romper


# --- editor y nuevos flujos del menú --------------------------------------


def test_menu_abre_editor(screen):
    from wom.ui.menu_screen import MenuScreen

    menu = MenuScreen()
    menu.draw(screen)
    _click_menu(menu, "editor")
    assert menu.take_action() == "editor"


def test_menu_origen_archivo_y_pick_map(screen, tmp_path, monkeypatch):
    import wom.persistence.scenario as scenario
    from wom.core.game import Player
    from wom.core.worldmap import Fort, Terrain, WorldMap
    from wom.ui.menu_screen import MenuScreen, NewGameChoice

    world = WorldMap(width=12, height=10, tiles=[[Terrain.PLAINS] * 12 for _ in range(10)])
    world.forts = [Fort((1, 1), owner=0), Fort((10, 8), owner=1)]
    doc = scenario.ScenarioDoc(
        world=world,
        players=[Player(0, "J"), Player(1, "R", is_ai=True, ai_level="medio")],
        army_specs=[{"owner": 0, "position": [1, 1], "composition": {"soldado": 5}}],
    )
    scenario.save_scenario(doc, name="mimapa", directory=tmp_path)
    monkeypatch.setattr(scenario, "MAPS_DIR", tmp_path)
    monkeypatch.setattr(scenario, "BUNDLED_SCENARIOS_DIR", tmp_path / "no_existe")

    menu = MenuScreen()
    menu.draw(screen)
    _click_menu(menu, "new")
    menu.draw(screen)
    _click_menu(menu, "map_source")  # aleatorio → archivo
    assert menu.map_source == "archivo"
    menu.draw(screen)
    _click_menu(menu, "pick_file")
    assert menu.mode == "pick_map"
    menu.draw(screen)
    _click_menu(menu, next(b for b in menu._buttons if b.startswith("map:")))
    assert menu.loaded_map_path is not None and menu.mode == "new"
    menu.draw(screen)
    _click_menu(menu, "start")
    action = menu.take_action()
    assert isinstance(action, NewGameChoice) and action.map_path is not None


def test_menu_escenarios_intro_y_jugar(screen, tmp_path, monkeypatch):
    import wom.persistence.scenario as scenario
    from wom.core.game import Player
    from wom.core.victory import VictoryMode
    from wom.core.worldmap import Fort, Terrain, WorldMap
    from wom.ui.menu_screen import MenuScreen, ScenarioChoice

    world = WorldMap(width=12, height=10, tiles=[[Terrain.PLAINS] * 12 for _ in range(10)])
    world.forts = [Fort((1, 1), owner=0), Fort((10, 8), owner=1)]
    doc = scenario.ScenarioDoc(
        world=world,
        players=[Player(0, "J"), Player(1, "R", is_ai=True, ai_level="dificil")],
        army_specs=[],
        title="El asedio",
        description="Defendé el fuerte del norte a toda costa.",
        victory_mode=VictoryMode.FLAGS,
        ai_level="dificil",
    )
    scenario.save_scenario(doc, name="asedio", directory=tmp_path)
    monkeypatch.setattr(scenario, "MAPS_DIR", tmp_path)
    monkeypatch.setattr(scenario, "BUNDLED_SCENARIOS_DIR", tmp_path / "no_existe")

    menu = MenuScreen()
    menu.draw(screen)
    _click_menu(menu, "scenarios")
    assert menu.mode == "scenarios"
    menu.draw(screen)
    _click_menu(menu, next(b for b in menu._buttons if b.startswith("scn:")))
    assert menu.mode == "scenario_intro"
    menu.draw(screen)  # título + descripción sin romper
    _click_menu(menu, "play")
    action = menu.take_action()
    assert isinstance(action, ScenarioChoice)


def test_editor_dibuja_y_pinta(screen):
    from wom.core.worldmap import Terrain
    from wom.ui.editor_screen import EditorScreen

    editor = EditorScreen(size="chico")
    editor.draw(screen)
    # elegir el pincel de montaña desde la paleta y pintar un tile del mapa
    editor.draw(screen)
    mountain_btn = editor._buttons[f"tool:terrain:{Terrain.MOUNTAIN.value}"]
    editor.handle_event(
        pygame.event.Event(pygame.MOUSEBUTTONDOWN, {"pos": mountain_btn.center, "button": 1})
    )
    assert editor.tool == f"terrain:{Terrain.MOUNTAIN.value}"
    tile_point = editor.renderer.tile_center((3, 3))
    editor.handle_event(
        pygame.event.Event(pygame.MOUSEBUTTONDOWN, {"pos": tile_point, "button": 1})
    )
    editor.handle_event(
        pygame.event.Event(pygame.MOUSEBUTTONUP, {"pos": tile_point, "button": 1})
    )
    assert editor.game.world.tiles[3][3] is Terrain.MOUNTAIN
    editor.draw(screen)


def test_editor_esc_pide_confirmacion(screen):
    from wom.ui.editor_screen import EditorScreen

    editor = EditorScreen(size="chico")
    editor.handle_event(pygame.event.Event(pygame.KEYDOWN, {"key": pygame.K_ESCAPE}))
    assert editor._confirm is not None and not editor.wants_menu
    editor.draw(screen)
    editor.handle_event(pygame.event.Event(pygame.KEYDOWN, {"key": pygame.K_s}))
    assert editor.wants_menu


def test_intro_de_escenario_modal_al_iniciar(screen):
    """Con un escenario, GameScreen muestra la intro modal y la traga hasta que
    un clic la cierra."""
    from wom.ui.scenario_intro_overlay import ScenarioIntroOverlay

    players = [Player(0, "Humano"), Player(1, "AI", is_ai=True)]
    game = Game.new(MapParams(seed=7), players, VictoryMode.TIME)
    intro = ScenarioIntroOverlay("El asedio", "Defendé el fuerte del norte.")
    gs = GameScreen(game, human_id=0, intro=intro)
    assert intro.visible
    gs.draw(screen)  # la intro modal se dibuja por encima sin romper

    # Modal: mientras está visible traga el input (un Enter no pasa el turno).
    gs.handle_event(pygame.event.Event(pygame.KEYDOWN, {"key": pygame.K_RETURN}))
    assert game.turn == 0 and not intro.visible  # Enter solo cerró la intro

    # Cerrada: el juego recupera el control (Enter pasa el turno).
    gs.handle_event(pygame.event.Event(pygame.KEYDOWN, {"key": pygame.K_RETURN}))
    assert game.turn == 1
    gs.draw(screen)


def test_intro_vacia_no_se_muestra(screen):
    """Un documento sin título/descripción/imagen no abre el modal."""
    from wom.ui.scenario_intro_overlay import ScenarioIntroOverlay

    intro = ScenarioIntroOverlay("", "", None)
    assert not intro.visible
    assert not intro.handle_event(
        pygame.event.Event(pygame.MOUSEBUTTONDOWN, {"pos": (0, 0), "button": 1})
    )
