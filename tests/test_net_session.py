"""Sesión de red sobre loopback: handshake, lobby, arranque y juego."""

import time

from wom.net.protocol import GameSetup
from wom.net.session import (
    ChatReceived,
    Connected,
    Disconnected,
    GameReady,
    HashReceived,
    HostSession,
    ClientSession,
    OrdersReceived,
    ReadyChanged,
    Rejected,
    SessionState,
    Started,
)
from wom.net.transport import Server, connect

CONFIG_HASH = "test-config-hash"


def _setup_provider(peer_name: str) -> GameSetup:
    return GameSetup(
        state={"turn": 0, "seed": 99},
        rules={"turn_seconds": 0, "max_turns": 50},
        names=["Host", peer_name],
    )


def _wait(predicate, timeout: float = 3.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return False


def _make_sessions(host_config=CONFIG_HASH, client_config=CONFIG_HASH):
    server = Server(host="127.0.0.1", port=0)
    client_conn = connect("127.0.0.1", server.port, timeout=3.0)
    assert _wait(lambda: server.poll_connection() is not None)
    host = HostSession(
        server.poll_connection(), "Host", _setup_provider, config_hash=host_config
    )
    client = ClientSession(client_conn, "Cliente", config_hash=client_config)
    return server, host, client


def _pump_until(sessions, predicate, timeout: float = 3.0):
    """Drena los eventos de todas las sesiones hasta que se cumpla el predicado.

    Devuelve un dict sesión → lista acumulada de eventos.
    """
    deadline = time.time() + timeout
    collected = {s: [] for s in sessions}
    while time.time() < deadline:
        for s in sessions:
            collected[s].extend(s.update())
        if predicate(collected):
            break
        time.sleep(0.005)
    return collected


def _has(events, event_type) -> bool:
    return any(isinstance(e, event_type) for e in events)


def _close_all(server, host, client):
    host.connection.close()
    client.connection.close()
    server.close()


def _reach_lobby(host, client):
    return _pump_until(
        [host, client],
        lambda c: _has(c[client], GameReady) and _has(c[host], Connected),
    )


def _reach_playing(host, client):
    host.set_ready(True)
    client.set_ready(True)
    return _pump_until(
        [host, client],
        lambda c: _has(c[host], Started) and _has(c[client], Started),
    )


def test_handshake_reaches_lobby_with_setup():
    server, host, client = _make_sessions()
    try:
        ev = _reach_lobby(host, client)
        assert _has(ev[host], Connected)
        assert _has(ev[client], Connected)  # Welcome aceptado
        assert host.state is SessionState.LOBBY
        assert client.state is SessionState.LOBBY
        setup = next(e.setup for e in ev[client] if isinstance(e, GameReady))
        assert setup.names == ["Host", "Cliente"]
        assert setup.state == {"turn": 0, "seed": 99}
        assert host.peer_name == "Cliente"
        assert client.peer_name == "Host"
    finally:
        _close_all(server, host, client)


def test_both_ready_starts_the_game():
    server, host, client = _make_sessions()
    try:
        _reach_lobby(host, client)
        ev = _reach_playing(host, client)
        assert _has(ev[host], Started)
        assert _has(ev[client], Started)
        assert host.state is SessionState.PLAYING
        assert client.state is SessionState.PLAYING
    finally:
        _close_all(server, host, client)


def test_ready_toggle_notifies_peer():
    server, host, client = _make_sessions()
    try:
        _reach_lobby(host, client)
        client.set_ready(True)
        ev = _pump_until([host, client], lambda c: _has(c[host], ReadyChanged))
        change = next(e for e in ev[host] if isinstance(e, ReadyChanged))
        assert change.name == "Cliente" and change.ready is True
    finally:
        _close_all(server, host, client)


def test_handshake_rejected_on_config_mismatch():
    server, host, client = _make_sessions(client_config="otra-config")
    try:
        ev = _pump_until([host, client], lambda c: _has(c[client], Rejected))
        rejected = next(e for e in ev[client] if isinstance(e, Rejected))
        assert "config" in rejected.reason.lower()
        assert client.state is SessionState.CLOSED
        assert host.state is SessionState.CLOSED
    finally:
        _close_all(server, host, client)


def test_orders_hash_and_chat_in_playing():
    server, host, client = _make_sessions()
    try:
        _reach_lobby(host, client)
        _reach_playing(host, client)
        host.send_orders(0, [{"kind": "create", "position": [1, 2]}])
        host.send_hash(0, "deadbeef")
        host.send_chat("hola rival")
        ev = _pump_until(
            [client],
            lambda c: _has(c[client], OrdersReceived)
            and _has(c[client], HashReceived)
            and _has(c[client], ChatReceived),
        )
        orders = next(e for e in ev[client] if isinstance(e, OrdersReceived))
        assert orders.turn == 0
        assert orders.orders == [{"kind": "create", "position": [1, 2]}]
        digest = next(e for e in ev[client] if isinstance(e, HashReceived))
        assert digest.digest == "deadbeef"
        chat = next(e for e in ev[client] if isinstance(e, ChatReceived))
        assert chat.name == "Host" and chat.text == "hola rival"
    finally:
        _close_all(server, host, client)


def test_cancel_notifies_peer_with_reason():
    server, host, client = _make_sessions()
    try:
        _reach_lobby(host, client)
        _reach_playing(host, client)
        host.cancel("el host canceló la partida")
        ev = _pump_until([client], lambda c: _has(c[client], Disconnected))
        disc = next(e for e in ev[client] if isinstance(e, Disconnected))
        assert "canceló" in disc.reason
        assert client.state is SessionState.CLOSED
    finally:
        _close_all(server, host, client)


def test_abrupt_disconnect_reported():
    server, host, client = _make_sessions()
    try:
        _reach_lobby(host, client)
        _reach_playing(host, client)
        client.connection.close()  # caída abrupta, sin Bye
        ev = _pump_until([host], lambda c: _has(c[host], Disconnected))
        assert _has(ev[host], Disconnected)
        assert host.state is SessionState.CLOSED
    finally:
        _close_all(server, host, client)
