"""Genera capturas del modo multijugador para el README/documentación.

- docs/screenshot_multiplayer.png: una partida en red (panel lateral con chat,
  rival conectado y reloj de turno) usando un `net` falso (sin sockets).
- docs/screenshot_multiplayer_lobby.png: la pantalla de crear partida en red.

Usa el driver dummy de SDL, así que no abre ventana.

Uso:
    python tools/screenshot_multiplayer.py
"""

import os

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

import pygame

from wom.core.game import Game, Player
from wom.core.mapgen import MapParams
from wom.core.victory import VictoryMode
from wom.ui import theme
from wom.ui.game_screen import GameScreen
from wom.ui.multiplayer_screen import MultiplayerScreen


class _FakeNet:
    """Net mínimo para dibujar la UI en red sin abrir sockets."""

    def __init__(self) -> None:
        self.waiting = False
        self.disconnected = False
        self.desync = False
        self.resynced = False
        self.peer_name = "Carla"
        self.chat_log = [
            ("Carla", "¡Hola! ¿Listo para la revancha?"),
            ("Fabio", "Dale, voy por tu fuerte del norte"),
            ("Carla", "Ja, lo veo bien defendido…"),
        ]

    def update(self) -> None:
        pass

    def consume_resolved(self):
        return None


def _docs_path(name: str) -> str:
    return os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "docs", name)
    )


def _game_in_net() -> None:
    pygame.init()
    screen = pygame.display.set_mode(theme.WINDOW_SIZE)
    players = [Player(0, "Fabio"), Player(1, "Carla")]
    game = Game.new(MapParams(seed=7), players, VictoryMode.TOTAL)
    gs = GameScreen(game, human_id=0, net=_FakeNet(), turn_seconds=60)

    # Un poco de movimiento para que el mapa no esté en su estado inicial.
    army = game.armies_of(0)[0]
    gs._left_click(gs.renderer.tile_center(army.position))
    x, y = army.position
    gs._left_click(gs.renderer.tile_center((x + 5, y + 2)))
    for _ in range(2):
        game.run_turn([])

    # Reloj de turno activo para que el panel lo muestre.
    gs._turn_deadline = pygame.time.get_ticks() + 42 * 1000

    # Seleccionar el ejército propio para mostrar su panel.
    own = game.armies_of(0)[0]
    gs._left_click(gs.renderer.tile_center(own.position))

    gs.draw(screen)
    pygame.image.save(screen, _docs_path("screenshot_multiplayer.png"))
    print("screenshot_multiplayer.png ok")


def _lobby() -> None:
    screen = pygame.display.get_surface() or pygame.display.set_mode(theme.WINDOW_SIZE)
    mp = MultiplayerScreen(default_name="Fabio")
    mp.mode = "create"
    mp.draw(screen)
    pygame.image.save(screen, _docs_path("screenshot_multiplayer_lobby.png"))
    print("screenshot_multiplayer_lobby.png ok")


def main() -> None:
    _game_in_net()
    _lobby()


if __name__ == "__main__":
    main()
