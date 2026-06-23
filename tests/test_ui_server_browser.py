"""Navegador de servidores (S6), headless con SDL dummy: lista persistida,
alta/edición/borrado, y conexión a un LobbyServer real en loopback."""

import os
import time

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

import pygame
import pytest

from wom.net.lobby import LobbyServer
from wom.net.transport import Server
from wom.persistence.settings import add_server, load_settings, remove_server, update_server
from wom.ui import theme
from wom.ui.menu_screen import MenuScreen
from wom.ui.multiplayer_screen import MultiplayerScreen
from wom.ui.server_browser_screen import ServerBrowserScreen


@pytest.fixture(scope="module")
def screen():
    pygame.init()
    yield pygame.display.set_mode(theme.WINDOW_SIZE)
    pygame.quit()


# --- persistencia (pura) ---------------------------------------------------


def test_helpers_de_lista_de_servidores():
    servers = []
    servers = add_server(servers, "A", "1.2.3.4", 50000)
    servers = add_server(servers, "B", "host.b", 51000)
    assert [s["name"] for s in servers] == ["A", "B"]
    servers = update_server(servers, 0, "A2", "1.2.3.4", 50001)
    assert servers[0] == {"name": "A2", "host": "1.2.3.4", "port": 50001}
    servers = remove_server(servers, 0)
    assert [s["name"] for s in servers] == ["B"]


def test_settings_round_trip_de_servidores(tmp_path):
    path = tmp_path / "settings.json"
    s = load_settings(path)
    s.servers = add_server(s.servers, "Mi srv", "wom.example", 50000)
    s.player_name = "Ana"
    from wom.persistence.settings import save_settings

    save_settings(s, path)
    reloaded = load_settings(path)
    assert reloaded.player_name == "Ana"
    assert reloaded.servers[0]["host"] == "wom.example"


# --- pantalla --------------------------------------------------------------


def test_draw_todos_los_modos(screen, tmp_path):
    sb = ServerBrowserScreen(settings_path=tmp_path / "s.json")
    for mode in ("browser", "form", "connecting", "lobby", "createform", "room"):
        sb.mode = mode
        sb.draw(screen)  # no debe romper


def test_alta_persiste_servidor(screen, tmp_path):
    path = tmp_path / "s.json"
    sb = ServerBrowserScreen(settings_path=path)
    assert sb.servers == []
    sb._activate("add")
    assert sb.mode == "form"
    sb.f_sname.value = "Mi server"
    sb.f_shost.value = "10.0.0.9"
    sb.f_sport.value = "50000"
    sb._activate("save_form")
    assert sb.mode == "browser"
    assert len(sb.servers) == 1 and sb.selected == 0
    # Persistió en el archivo.
    assert load_settings(path).servers[0]["host"] == "10.0.0.9"


def test_editar_y_borrar(screen, tmp_path):
    path = tmp_path / "s.json"
    sb = ServerBrowserScreen(settings_path=path)
    sb._activate("add")
    sb.f_shost.value = "h1"
    sb._activate("save_form")
    sb.selected = 0
    sb._activate("edit")
    sb.f_shost.value = "h1-edit"
    sb._activate("save_form")
    assert sb.servers[0]["host"] == "h1-edit"
    sb._activate("delete")
    assert sb.servers == []
    assert sb.selected is None


def test_menu_y_hub_llevan_al_navegador(screen):
    mp = MultiplayerScreen()
    mp.draw(screen)
    mp._activate("to_internet")
    assert mp.wants_internet is True


def test_conecta_a_un_lobbyserver_real(screen, tmp_path):
    server = Server(host="127.0.0.1", port=0, max_clients=4)
    lobby = LobbyServer(name="SrvTest", server=server)  # config_hash real (coincide)
    sb = ServerBrowserScreen(settings_path=tmp_path / "s.json")
    sb.f_player.value = "Ana"
    sb.servers = add_server(sb.servers, "Local", "127.0.0.1", server.port)
    sb.selected = 0
    try:
        sb._activate("connect")
        assert sb.session is not None and sb.mode == "connecting"
        # Bombear ambos lados hasta entrar al lobby.
        deadline = time.time() + 4.0
        while time.time() < deadline:
            lobby.update()
            sb.update()
            # El roster (LobbyState) llega un instante después del Welcome.
            if sb.mode == "lobby" and sb.session.players:
                break
            time.sleep(0.005)
        assert sb.mode == "lobby"
        assert sb.session.server_name == "SrvTest"
        # El roster muestra al jugador.
        assert any(row[1] == "Ana" for row in sb.session.players)
        sb.draw(screen)  # el lobby dibuja sin romper
    finally:
        sb._disconnect()
        lobby.shutdown()
        server.close()


def test_flujo_crear_listo_arranca_contra_servidor(screen, tmp_path):
    """Crear partida → entrar a la sala → con un 2º jugador y todos listos, el
    navegador arma el `net_start` para lanzar la partida en red."""
    from server.config import ServerConfig
    from server.game_server import GameServer
    from wom.net.server_session import ServerSession
    from wom.net.transport import connect

    gs = GameServer(ServerConfig(host="127.0.0.1", port=0))
    a = ServerBrowserScreen(settings_path=tmp_path / "s.json")
    a.f_player.value = "Ana"
    a.servers = add_server(a.servers, "Local", "127.0.0.1", gs.port)
    a.selected = 0
    b = None

    def pump(pred, extra=(), timeout=6.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            gs.tick()
            a.update()
            for s in extra:
                s.update()
            if pred():
                return True
            time.sleep(0.005)
        return False

    try:
        a._activate("connect")
        assert pump(lambda: a.mode == "lobby")
        a.create_players = 2
        a._create_match()
        assert pump(lambda: a.mode == "room"), "no entró a la sala"
        a._activate("ready")  # Ana lista

        b = ServerSession(connect("127.0.0.1", gs.port, timeout=3.0), "Beto")
        assert pump(lambda: bool(b.matches), extra=[b]), "Beto no vio el catálogo"
        b.join_match(b.matches[0][0])
        assert pump(lambda: b.match_id is not None, extra=[b]), "Beto no se unió"
        b.set_ready(True)

        assert pump(lambda: a.net_start is not None, extra=[b]), "la partida no arrancó"
        assert a.net_start.role == "client"
        assert a.net_start.human_id == 0
        assert a.net_start.game is not None
    finally:
        if b is not None:
            b.cancel()
        a._disconnect()
        gs.transport.close()
