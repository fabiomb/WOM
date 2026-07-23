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
from wom.persistence.settings import Settings, load_settings
from wom.ui import theme
from wom.ui.menu_screen import MenuScreen
from wom.ui.multiplayer_screen import MultiplayerScreen, TextField


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
    mp = MultiplayerScreen(settings=Settings())
    for mode in ("hub", "create", "connect", "llm_create", "llm_config"):
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


# --- rival LLM --------------------------------------------------------------


def test_hub_tiene_seccion_llm(screen):
    mp = MultiplayerScreen(settings=Settings())
    mp.draw(screen)
    mp.handle_event(
        pygame.event.Event(
            pygame.MOUSEBUTTONDOWN, {"pos": mp._buttons["to_llm_create"].center, "button": 1}
        )
    )
    assert mp.mode == "llm_create"
    mp.mode = "hub"
    mp.draw(screen)
    mp._activate("to_llm_config")
    assert mp.mode == "llm_config"


def test_pegar_con_ctrl_v(screen, monkeypatch):
    import wom.ui.multiplayer_screen as mps

    monkeypatch.setattr(mps, "clipboard_get", lambda: "sk-una-key-larga-1234567890")
    mp = MultiplayerScreen(settings=Settings())
    mp.mode = "llm_config"
    mp.draw(screen)  # registra los rects de los campos
    mp.handle_event(
        pygame.event.Event(
            pygame.MOUSEBUTTONDOWN, {"pos": mp._fields["llm_apikey"].center, "button": 1}
        )
    )
    assert mp.focused == "llm_apikey"
    mp.f_llm_apikey.value = ""
    mp.handle_event(
        pygame.event.Event(
            pygame.KEYDOWN,
            {"key": pygame.K_v, "mod": pygame.KMOD_CTRL, "unicode": "\x16"},
        )
    )
    assert mp.f_llm_apikey.value == "sk-una-key-larga-1234567890"


def test_pegar_respeta_numeric_y_max_len(monkeypatch):
    field = TextField("", numeric=True, max_len=4)
    field.paste("a1b2c3d4e5")
    assert field.value == "1234"
    copied = {}
    import wom.ui.multiplayer_screen as mps

    monkeypatch.setattr(mps, "clipboard_put", lambda t: copied.setdefault("v", t))
    field.key(
        pygame.event.Event(
            pygame.KEYDOWN, {"key": pygame.K_c, "mod": pygame.KMOD_CTRL, "unicode": "\x03"}
        )
    )
    assert copied["v"] == "1234"


def test_guardar_config_llm(tmp_path, screen):
    path = tmp_path / "settings.json"
    mp = MultiplayerScreen(settings=Settings(), settings_path=path)
    mp.llm_provider = "anthropic"
    mp.f_llm_model.value = "claude-fable-5"
    mp.f_llm_name.value = "Claude"
    mp.llm_effort = "high"
    mp.f_llm_apikey.value = "sk-123"
    mp._activate("llm_save")
    loaded = load_settings(path)
    assert loaded.llm_provider == "anthropic"
    assert loaded.llm_model == "claude-fable-5"
    assert loaded.llm_name == "Claude"
    assert loaded.llm_effort == "high"
    assert loaded.llm_api_key == "sk-123"
    # La config del backend refleja el formulario (effort → thinking).
    config = mp._llm_backend_config()
    assert config.provider == "anthropic" and config.thinking and config.effort == "high"


def test_partida_llm_sin_api_key_no_arranca(screen, monkeypatch):
    """Un proveedor con key obligatoria y sin key configurada frena antes de
    abrir el server, con el aviso a la vista (si no, el LLM jugaría pasando
    todos los turnos en silencio)."""
    for env in ("GEMINI_API_KEY", "GOOGLE_API_KEY"):
        monkeypatch.delenv(env, raising=False)
    mp = MultiplayerScreen(settings=Settings())
    mp.mode = "llm_create"
    mp.llm_provider = "gemini"
    mp.f_llm_apikey.value = ""
    mp._activate("llm_start")
    assert mp.mode == "llm_create" and mp.server is None and mp.llm_runner is None
    assert "API key" in mp.status


def test_partida_llm_local_llega_a_started(screen):
    """Con un proveedor local (ollama, sin key) la partida vs LLM arma el host
    loopback, el runner se conecta y ambos quedan listos: mode → started con el
    runner colgado del NetGameStart. (El backend no se llama en el lobby, así
    que no hace falta un Ollama real.)"""
    mp = MultiplayerScreen(settings=Settings())
    mp.mode = "llm_create"
    mp.llm_provider = "ollama"
    mp.f_llm_model.value = "gemma3"
    mp.f_llm_name.value = "Bot"
    mp._activate("llm_start")
    assert mp.mode == "waiting" and mp.llm_runner is not None
    try:
        assert _pump([mp], lambda: mp.mode == "started"), (
            f"no arrancó la partida vs LLM (runner: {mp.llm_runner and mp.llm_runner.status})"
        )
        assert mp.net_start is not None
        assert mp.net_start.llm_runner is mp.llm_runner
        assert mp.net_start.game.players[1].name == "Bot"
    finally:
        if mp.llm_runner is not None:
            mp.llm_runner.stop()
        mp._teardown()


def test_cancelar_vuelve_al_hub(screen):
    host = MultiplayerScreen("Host")
    host.mode = "create"
    host.f_hostport.value = "0"
    host._activate("host_start")
    assert host.mode == "waiting"
    host._activate("cancel")
    assert host.mode == "hub"
    assert host.server is None and host.session is None
