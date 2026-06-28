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
        # El host ve al rival en su roster; el cliente conoce el nombre del host.
        assert any(name == "Cliente" for _pid, name, _r in host.session.roster())
        assert client.session.peer_name == "Host"

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


def test_lobby_cuatro_jugadores(screen):
    """El host configura 4 jugadores y espera a que se conecten los 3 rivales;
    con todos listos arranca y cada uno reconstruye el mismo estado inicial."""
    host = MultiplayerScreen("Host")
    host.mode = "create"
    host.f_hostport.value = "0"
    host.n_players = 4
    host._activate("host_start")
    port = host.server.port

    clients = []
    for i in range(3):
        c = MultiplayerScreen(f"C{i + 1}")
        c.mode = "connect"
        c.f_ip.value = "127.0.0.1"
        c.f_connectport.value = str(port)
        c._activate("connect_start")
        clients.append(c)

    screens = [host, *clients]
    try:
        # Esperar a que TODOS lleguen al lobby (el host recién manda el setup
        # cuando están los 3 rivales).
        assert _pump(
            screens,
            lambda: all(
                s.session is not None and s.session.state is SessionState.LOBBY
                for s in screens
            ),
        ), "no se alcanzó el lobby con 4 jugadores"
        assert len(host.session.roster()) == 4

        for s in screens:
            s._activate("ready")
        assert _pump(
            screens, lambda: all(s.mode == "started" for s in screens)
        ), "no arrancó la partida de 4"

        assert host.net_start.human_id == 0
        assert sorted(c.net_start.human_id for c in clients) == [1, 2, 3]
        ref = host.net_start.game.to_dict()
        assert all(c.net_start.game.to_dict() == ref for c in clients)
        assert len(host.net_start.game.players) == 4
    finally:
        for s in screens:
            s._teardown()


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


def test_chat_de_la_sala(screen):
    """En la sala de espera, enviar un mensaje lo refleja localmente y el rival
    lo recibe (el chat ya viaja por la sesión del lobby)."""
    host = MultiplayerScreen("Host")
    host.mode = "create"
    host.f_hostport.value = "0"
    host._activate("host_start")
    port = host.server.port

    client = MultiplayerScreen("Cliente")
    client.mode = "connect"
    client.f_ip.value = "127.0.0.1"
    client.f_connectport.value = str(port)
    client._activate("connect_start")

    try:
        assert _pump(
            [host, client],
            lambda: host.session is not None
            and host.session.state is SessionState.LOBBY
            and client.session is not None
            and client.session.state is SessionState.LOBBY,
        ), "no se alcanzó el lobby"

        # El host escribe y envía con Enter: se ve de inmediato en su propio log.
        host.focused = "chat"
        for ch in "hola":
            host.handle_event(pygame.event.Event(pygame.KEYDOWN, {"key": 0, "unicode": ch}))
        host.handle_event(pygame.event.Event(pygame.KEYDOWN, {"key": pygame.K_RETURN, "unicode": ""}))
        assert host.f_chat.value == "" and host.focused == "chat"  # listo para seguir
        assert ("Host", "hola") in host.chat_log

        # El cliente lo recibe al bombear la sesión.
        assert _pump(
            [host, client],
            lambda: any(text == "hola" for _name, text in client.chat_log),
        ), "el cliente no recibió el chat"

        client.mode = "waiting"
        client.draw(screen)  # dibuja el panel de chat sin romper
    finally:
        host._teardown()
        client._teardown()


def test_cancelar_vuelve_al_hub(screen):
    host = MultiplayerScreen("Host")
    host.mode = "create"
    host.f_hostport.value = "0"
    host._activate("host_start")
    assert host.mode == "waiting"
    host._activate("cancel")
    assert host.mode == "hub"
    assert host.server is None and host.session is None
