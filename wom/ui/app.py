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
from wom.net.lockstep import NetGame
from wom.persistence.savegame import load_game
from wom.persistence.scenario import build_game, load_scenario
from wom.persistence.settings import load_settings
from wom.ui import scale, theme
from wom.ui.editor_screen import EditorScreen
from wom.ui.game_screen import GameScreen
from wom.ui.menu_screen import (
    LoadChoice,
    MenuScreen,
    NewGameChoice,
    ScenarioChoice,
)
from wom.ui.multiplayer_screen import MultiplayerScreen
from wom.ui.music import MusicPlayer
from wom.ui.music_overlay import MusicOverlay
from wom.ui.video import apply_video_settings, parse_resolution

WINDOW_TITLE = "WOM"
HUMAN_ID = 0


def _event_to_overlay(current, overlay, event) -> bool:
    """¿Este evento lo debe ver primero el reproductor modal (atajos globales)?

    Si la pantalla activa está capturando texto (chat, campos editables) y el
    reproductor está cerrado, las **teclas** van directo a la pantalla: así
    escribir una "m" no abre el reproductor. El mouse y el caso con el
    reproductor ya abierto (modal) siguen el camino normal.
    """
    capturing = getattr(current, "capturing_text", False)
    if capturing and not overlay.visible and event.type in (
        pygame.KEYDOWN, pygame.KEYUP, pygame.TEXTINPUT
    ):
        return False
    return True


def new_game(choice: NewGameChoice) -> Game:
    """Crea una partida humano vs AI según lo elegido en el menú.

    Con `map_path` ("Cargar mapa") se usa el terreno+tropas de un `.wom` y el
    menú impone jugadores y victoria; si no, se genera un mapa aleatorio.
    """
    players = [
        Player(HUMAN_ID, "Jugador"),
        Player(1, f"AI ({choice.ai_level})", is_ai=True, ai_level=choice.ai_level),
    ]
    if choice.map_path is not None:
        return build_game(
            load_scenario(choice.map_path),
            players=players,
            victory_mode=choice.victory_mode,
        )
    return Game.new(choice.map_params(), players, choice.victory_mode)


def scenario_game(choice: ScenarioChoice) -> Game:
    """Arranca un escenario completo: honra la IA y la victoria del `.wom`."""
    return build_game(load_scenario(choice.path))


def _start_net_game(net_start) -> GameScreen:
    """Arranca la partida en red a partir del lobby (NetGameStart)."""
    net = NetGame(
        net_start.session,
        net_start.game,
        net_start.human_id,
        is_host=(net_start.role == "host"),
        peer_name=net_start.peer_name,
    )
    return GameScreen(
        net_start.game,
        human_id=net_start.human_id,
        net=net,
        turn_seconds=net_start.rules.turn_seconds,
    )


def run(seed: int | None = None, ai_level: str = "medio") -> None:
    """Punto de entrada de la aplicación gráfica. Arranca en el menú."""
    pygame.init()
    # La ventana es redimensionable/maximizable y arranca con la resolución
    # guardada; el juego dibuja siempre en un canvas lógico (WINDOW_SIZE)
    # que scale.present() estira a la ventana manteniendo la proporción.
    settings = load_settings()
    pygame.display.set_mode(parse_resolution(settings.video_resolution), pygame.RESIZABLE)
    pygame.display.set_caption(WINDOW_TITLE)
    if settings.video_maximized:
        apply_video_settings(settings)
    canvas = pygame.Surface(theme.WINDOW_SIZE)
    clock = pygame.time.Clock()

    music = MusicPlayer(settings=settings)
    music.start()  # un tema al azar desde el arranque (si está habilitada)
    music_overlay = MusicOverlay(music)

    current: MenuScreen | GameScreen | MultiplayerScreen = MenuScreen(
        ai_level, seed, music=music
    )
    running = True
    while running:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                # Cerrar la ventana en plena partida en red: avisar al rival
                # (Bye) para que no quede esperando órdenes.
                if isinstance(current, GameScreen) and current.net is not None:
                    current.net.session.cancel("el rival cerró el juego")
                running = False
            elif music.handle_event(event):
                pass  # fin del tema: la playlist ya avanzó
            else:
                event = scale.translate_event(event)  # mouse físico → lógico
                consumed = (
                    music_overlay.handle_event(event)
                    if _event_to_overlay(current, music_overlay, event)
                    else False
                )
                if not consumed:
                    current.handle_event(event)

        if isinstance(current, MenuScreen):
            action = current.take_action()
            if action == "quit":
                running = False
            elif action == "multiplayer":
                current = MultiplayerScreen()
            elif action == "editor":
                current = EditorScreen()
            elif isinstance(action, NewGameChoice):
                current = GameScreen(new_game(action), human_id=HUMAN_ID)
            elif isinstance(action, ScenarioChoice):
                current = GameScreen(scenario_game(action), human_id=HUMAN_ID)
            elif isinstance(action, LoadChoice):
                current = GameScreen(load_game(action.path), human_id=HUMAN_ID)
        elif isinstance(current, MultiplayerScreen):
            current.update()  # conduce la red (lobby) una vez por frame
            if current.net_start is not None:
                current = _start_net_game(current.net_start)
            elif current.wants_menu:
                current = MenuScreen(ai_level, seed, music=music)
        elif isinstance(current, EditorScreen):
            if current.wants_menu:
                current = MenuScreen(ai_level, seed, music=music)
        else:  # GameScreen
            current.update()  # conduce el lockstep en red (no-op sin red)
            if current.wants_menu:
                current = MenuScreen(ai_level, seed, music=music)

        current.draw(canvas)
        music_overlay.draw(canvas)
        scale.present(pygame.display.get_surface(), canvas)
        pygame.display.flip()
        clock.tick(theme.FPS)

    pygame.quit()
