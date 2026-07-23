"""Aplicación pygame: loop principal y cambio de pantallas (menú ↔ partida).

El menú decide qué partida arrancar (nueva con parámetros o cargada de un
savegame); la partida vuelve al menú con ESC. Guardar está disponible
durante la partida (botón del HUD o tecla G). La música arranca con la app
(un tema al azar de la carpeta configurada) y la tecla M abre el reproductor
modal por encima de cualquier pantalla.
"""

from __future__ import annotations

import pygame

from wom.ai.ai_player import AIPlayer, choose_personality
from wom.core.config import load_ai_personalities
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
from wom.ui.server_browser_screen import ServerBrowserScreen
from wom.ui.music import MusicPlayer
from wom.ui.sound import SoundPlayer
from wom.ui.scenario_intro_overlay import ScenarioIntroOverlay
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
    menú impone victoria; la cantidad de jugadores la fija el mapa (sus dueños)
    y los niveles de IA elegidos se reparten entre los rivales. Si no, se genera
    un mapa aleatorio con un rival IA por cada nivel elegido.
    """
    if choice.map_path is not None:
        doc = load_scenario(choice.map_path)
        return build_game(
            doc,
            players=_players_for_map(doc, choice.ai_levels),
            victory_mode=choice.victory_mode,
        )
    players = [Player(HUMAN_ID, "Jugador")]
    for i, level in enumerate(choice.ai_levels, start=1):
        players.append(Player(i, f"IA {i} ({level})", is_ai=True, ai_level=level))
    return Game.new(choice.map_params(), players, choice.victory_mode)


def _players_for_map(doc, ai_levels: tuple[str, ...]) -> list[Player]:
    """Jugadores para un mapa cargado: la cantidad la fija el mapa (el mayor id
    de dueño presente en fuertes/tropas); el 0 es el humano y el resto IA, con
    los niveles elegidos repartidos en orden (cíclico si faltan)."""
    owners = {f.owner for f in doc.world.forts if f.owner >= 0}
    owners |= {int(a["owner"]) for a in doc.army_specs if int(a["owner"]) >= 0}
    n_players = max(owners) + 1 if owners else 2
    levels = ai_levels or ("medio",)
    players = [Player(HUMAN_ID, "Jugador")]
    for i in range(1, n_players):
        level = levels[(i - 1) % len(levels)]
        players.append(Player(i, f"IA {i} ({level})", is_ai=True, ai_level=level))
    return players


def scenario_game(choice: ScenarioChoice, music=None, sound=None) -> GameScreen:
    """Arranca un escenario completo: honra la IA y la victoria del `.wom` y
    muestra su intro (título/descripción/imagen) sobre el mapa al empezar."""
    doc = load_scenario(choice.path)
    intro = ScenarioIntroOverlay(doc.title, doc.description, doc.image_bytes)
    return GameScreen(
        build_game(doc), human_id=HUMAN_ID, intro=intro, music=music, sound=sound
    )


def _start_net_game(net_start, music=None, sound=None) -> GameScreen:
    """Arranca la partida en red a partir del lobby (NetGameStart).

    El host recibe un `ai_factory`: si un rival se cae, la IA lo controla hasta
    que vuelva (la personalidad sale de la seed, como en un jugador)."""
    is_host = net_start.role == "host"
    ai_factory = None
    if is_host:
        personalities = list(load_ai_personalities())
        seed = net_start.game.seed

        def ai_factory(player_id, _seed=seed, _pers=personalities):
            return AIPlayer(
                player_id, "medio", personality=choose_personality(_seed, player_id, _pers)
            )

    net = NetGame(
        net_start.session,
        net_start.game,
        net_start.human_id,
        is_host=is_host,
        peer_name=net_start.peer_name,
        ai_factory=ai_factory,
        tactical_mode=net_start.rules.tactical_mode,
    )
    return GameScreen(
        net_start.game,
        human_id=net_start.human_id,
        net=net,
        turn_seconds=net_start.rules.turn_seconds,
        music=music,
        sound=sound,
        llm_runner=getattr(net_start, "llm_runner", None),
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
    # Efectos de sonido: comparten el mismo `settings` que la música, así el
    # menú de Opciones guarda ambos sin pisarse (una sola instancia de Settings).
    sound = SoundPlayer(settings=settings)

    current: MenuScreen | GameScreen | MultiplayerScreen = MenuScreen(
        ai_level, seed, music=music, sound=sound
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
            next_screen: MenuScreen | GameScreen | MultiplayerScreen | None = None
            if action == "quit":
                running = False
            elif action == "multiplayer":
                # Comparte la instancia de Settings de la app: guardar la
                # config del LLM no pisa música/sonido ni viceversa.
                next_screen = MultiplayerScreen(settings=settings)
            elif action == "editor":
                next_screen = EditorScreen()
            elif isinstance(action, NewGameChoice):
                next_screen = GameScreen(
                    new_game(action), human_id=HUMAN_ID, music=music, sound=sound
                )
            elif isinstance(action, ScenarioChoice):
                next_screen = scenario_game(action, music=music, sound=sound)
            elif isinstance(action, LoadChoice):
                next_screen = GameScreen(
                    load_game(action.path), human_id=HUMAN_ID, music=music, sound=sound
                )
            if next_screen is not None:
                current.close()  # corta el video de portada (ffmpeg + hilo)
                current = next_screen
        elif isinstance(current, MultiplayerScreen):
            current.update()  # conduce la red (lobby) una vez por frame
            if current.net_start is not None:
                current = _start_net_game(current.net_start, music=music, sound=sound)
            elif current.wants_internet:
                current = ServerBrowserScreen()
            elif current.wants_menu:
                current = MenuScreen(ai_level, seed, music=music, sound=sound)
        elif isinstance(current, ServerBrowserScreen):
            current.update()  # conduce la sesión con el servidor dedicado
            if current.net_start is not None:
                current = _start_net_game(current.net_start, music=music, sound=sound)
            elif current.wants_menu:
                current = MenuScreen(ai_level, seed, music=music, sound=sound)
        elif isinstance(current, EditorScreen):
            if current.wants_menu:
                current = MenuScreen(ai_level, seed, music=music, sound=sound)
        else:  # GameScreen
            current.update()  # conduce el lockstep en red (no-op sin red)
            if getattr(current, "wants_lobby", False) and current.net is not None:
                # Partida del servidor dedicado: vuelve al lobby con la sesión viva.
                current = ServerBrowserScreen(session=current.net.session)
            elif current.wants_menu:
                current = MenuScreen(ai_level, seed, music=music, sound=sound)

        current.draw(canvas)
        music_overlay.draw(canvas)
        scale.present(pygame.display.get_surface(), canvas)
        pygame.display.flip()
        clock.tick(theme.FPS)

    if isinstance(current, MenuScreen):
        current.close()  # corta el video de portada si se salió desde el menú
    pygame.quit()
