"""Smoke + lobby de la pantalla de Multijugador (MP3), headless con SDL dummy.

Levanta dos pantallas (host + cliente) sobre loopback y las conduce por todo el
flujo de lobby hasta "listo para jugar", verificando que ambos lados quedan
sincronizados (mismo estado inicial reconstruido).
"""

import os
import time

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

import pygame
import pytest

from wom.net.session import SessionState
from wom.ui import theme
from wom.ui.menu_screen import MenuScreen
from wom.ui.multiplayer_screen import MultiplayerScreen


@pytest.fixture(scope="module")
def screen():
    pygame.init()
    yield pygame.display.set_mode(theme.WINDOW_SIZE)
    pygame.quit()


def _pump(screens, predicate, timeout: float = 5.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        for s in screens:
            s.update()
        if predicate():
            return True
        time.sleep(0.01)
    return False


def test_menu_tiene_opcion_multijugador(screen):
    menu = MenuScreen()
    menu.draw(screen)
    point = menu._buttons["multiplayer"].center
    menu.handle_event(
        pygame.event.Event(pygame.MOUSEBUTTONDOWN, {"pos": point, "button": 1})
    )
    assert menu.take_action() == "multiplayer"


def test_draw_todos_los_modos_sin_romper(screen):
    mp = MultiplayerScreen()
    for mode in ("hub", "create", "connect"):
        mp.mode = mode
        mp.draw(screen)  # no debe lanzar


def test_edicion_de_campo_de_texto(screen):
    mp = MultiplayerScreen("Ana")
    mp.mode = "connect"
    mp.draw(screen)  # registra los rects de los campos
    # foco en el campo IP por click
    mp.handle_event(
        pygame.event.Event(
            pygame.MOUSEBUTTONDOWN, {"pos": mp._fields["ip"].center, "button": 1}
        )
    )
    assert mp.focused == "ip"
    mp.f_ip.value = ""
    for ch in "10.0.0.5":
        mp.handle_event(
            pygame.event.Event(pygame.KEYDOWN, {"key": 0, "unicode": ch})
        )
    assert mp.f_ip.value == "10.0.0.5"
    # un campo numérico ignora no-dígitos
    mp.handle_event(
        pygame.event.Event(
            pygame.MOUSEBUTTONDOWN, {"pos": mp._fields["connectport"].center, "button": 1}
        )
    )
    mp.f_connectport.value = ""
    for ch in "5a0":
        mp.handle_event(pygame.event.Event(pygame.KEYDOWN, {"key": 0, "unicode": ch}))
    assert mp.f_connectport.value == "50"


def test_lobby_completo_hasta_listo(screen):
    host = MultiplayerScreen("Host")
    host.mode = "create"
    host.f_hostport.value = "0"  # puerto libre elegido por el SO
    host._activate("host_start")
    assert host.mode == "waiting" and host.server is not None
    port = host.server.port

    client = MultiplayerScreen("Cliente")
    client.mode = "connect"
    client.f_ip.value = "127.0.0.1"
    client.f_connectport.value = str(port)
    client._activate("connect_start")
    assert client.mode == "waiting" and client.session is not None

    try:
        # Handshake: ambos llegan al lobby.
        assert _pump(
            [host, client],
            lambda: host.session is not None
            and host.session.state is SessionState.LOBBY
            and client.session is not None
            and client.session.state is SessionState.LOBBY,
        ), "no se alcanzó el lobby"
        assert host.peer_name == "Cliente"
        assert client.peer_name == "Host"

        # Ambos marcan "Listo" → arranca.
        host._activate("ready")
        client._activate("ready")
        assert _pump(
            [host, client],
            lambda: host.mode == "started" and client.mode == "started",
        ), "no arrancó la partida"

        assert host.net_start is not None and host.net_start.human_id == 0
        assert host.net_start.role == "host"
        assert client.net_start is not None and client.net_start.human_id == 1
        assert client.net_start.role == "client"
        # El cliente reconstruyó exactamente el mismo estado inicial.
        assert host.net_start.game.to_dict() == client.net_start.game.to_dict()
        assert host.net_start.game.players[1].name == "Cliente"
    finally:
        host._teardown()
        client._teardown()


def test_conexion_fallida_no_rompe(screen):
    client = MultiplayerScreen("Solo")
    client.mode = "connect"
    client.f_ip.value = "127.0.0.1"
    client.f_connectport.value = "1"  # puerto cerrado
    client._activate("connect_start")
    # Sigue en la pantalla de conectar, con un aviso, sin sesión.
    assert client.mode == "connect"
    assert client.session is None
    assert "No se pudo conectar" in client.status
    client.draw(screen)


def test_cancelar_vuelve_al_hub(screen):
    host = MultiplayerScreen("Host")
    host.mode = "create"
    host.f_hostport.value = "0"
    host._activate("host_start")
    assert host.mode == "waiting"
    host._activate("cancel")
    assert host.mode == "hub"
    assert host.server is None and host.session is None
