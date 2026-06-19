"""Sesión de red sobre loopback: handshake, lobby, arranque y relay (N jugadores)."""

import time

from wom.net.protocol import GameSetup
from wom.net.session import (
    ChatReceived,
    ClientSession,
    Connected,
    Disconnected,
    GameReady,
    HashReceived,
    HostSession,
    OrdersReceived,
    Rejected,
    SessionState,
    Started,
    TurnReady,
)
from wom.net.transport import Server, connect

CONFIG_HASH = "test-config-hash"


def _setup_provider(names: list[str]) -> GameSetup:
    return GameSetup(
        state={"turn": 0, "seed": 99},
        rules={"turn_seconds": 0, "max_turns": 50},
        names=list(names),
    )


def _make_group(n_players: int = 2, client_configs=None):
    """Host + (n_players-1) clientes sobre loopback (sin handshake aún)."""
    server = Server(host="127.0.0.1", port=0, max_clients=n_players - 1)
    host = HostSession(n_players, "Host", _setup_provider, config_hash=CONFIG_HASH)
    clients = []
    for i in range(n_players - 1):
        conn = connect("127.0.0.1", server.port, timeout=3.0)
        cfg = CONFIG_HASH if client_configs is None else client_configs[i]
        clients.append(ClientSession(conn, f"C{i + 1}", config_hash=cfg))
    return server, host, clients


def _pump(server, host, clients, predicate, timeout: float = 3.0):
    """Drena eventos (alimentando al host las conexiones nuevas) hasta el predicado."""
    fed: set = set()
    collected = {host: []}
    for c in clients:
        collected[c] = []
    deadline = time.time() + timeout
    while time.time() < deadline:
        for conn in server.poll_connections():
            if conn not in fed:
                fed.add(conn)
                host.add_connection(conn)
        collected[host].extend(host.update())
        for c in clients:
            collected[c].extend(c.update())
        if predicate(collected):
            break
        time.sleep(0.005)
    return collected


def _has(events, event_type) -> bool:
    return any(isinstance(e, event_type) for e in events)


def _close_all(server, host, clients):
    host.cancel()
    for c in clients:
        c.connection.close()
    server.close()


def _reach_lobby(server, host, clients):
    return _pump(
        server, host, clients,
        lambda c: all(_has(c[cl], GameReady) for cl in clients)
        and host.state is SessionState.LOBBY,
    )


def _reach_playing(server, host, clients):
    host.set_ready(True)
    for c in clients:
        c.set_ready(True)
    return _pump(
        server, host, clients,
        lambda c: _has(c[host], Started) and all(_has(c[cl], Started) for cl in clients),
    )


def test_handshake_reaches_lobby_with_setup():
    server, host, clients = _make_group(2)
    client = clients[0]
    try:
        ev = _reach_lobby(server, host, clients)
        assert _has(ev[host], Connected)
        assert _has(ev[client], Connected)  # Welcome aceptado
        assert host.state is SessionState.LOBBY and client.state is SessionState.LOBBY
        setup = next(e.setup for e in ev[client] if isinstance(e, GameReady))
        assert setup.names == ["Host", "C1"]
        assert setup.human_id == 1  # el host le asignó el id 1
        assert setup.state == {"turn": 0, "seed": 99}
        assert client.peer_name == "Host"
    finally:
        _close_all(server, host, clients)


def test_both_ready_starts_the_game():
    server, host, clients = _make_group(2)
    try:
        _reach_lobby(server, host, clients)
        ev = _reach_playing(server, host, clients)
        assert _has(ev[host], Started) and _has(ev[clients[0]], Started)
        assert host.state is SessionState.PLAYING
        assert clients[0].state is SessionState.PLAYING
    finally:
        _close_all(server, host, clients)


def test_cuatro_jugadores_lobby_y_arranque():
    """Host + 3 clientes: ids 1,2,3, todos llegan al lobby y arrancan juntos."""
    server, host, clients = _make_group(4)
    try:
        ev = _reach_lobby(server, host, clients)
        ids = sorted(
            next(e.setup.human_id for e in ev[c] if isinstance(e, GameReady))
            for c in clients
        )
        assert ids == [1, 2, 3]
        assert len(host.roster()) == 4
        ev = _reach_playing(server, host, clients)
        assert host.state is SessionState.PLAYING
        assert all(c.state is SessionState.PLAYING for c in clients)
    finally:
        _close_all(server, host, clients)


def test_ready_se_refleja_en_el_roster():
    server, host, clients = _make_group(2)
    try:
        _reach_lobby(server, host, clients)
        clients[0].set_ready(True)
        _pump(
            server, host, clients,
            lambda c: any(r[2] for r in host.roster() if r[0] == 1),
        )
        assert any(r[0] == 1 and r[2] for r in host.roster())  # el rival quedó listo
    finally:
        _close_all(server, host, clients)


def test_handshake_rejected_on_config_mismatch():
    server, host, clients = _make_group(2, client_configs=["otra-config"])
    client = clients[0]
    try:
        ev = _pump(server, host, clients, lambda c: _has(c[client], Rejected))
        rejected = next(e for e in ev[client] if isinstance(e, Rejected))
        assert "config" in rejected.reason.lower()
        assert client.state is SessionState.CLOSED
    finally:
        _close_all(server, host, clients)


def test_relay_de_ordenes_hash_chat_y_bundle():
    server, host, clients = _make_group(2)
    client = clients[0]
    try:
        _reach_lobby(server, host, clients)
        _reach_playing(server, host, clients)
        # cliente → host: órdenes, hash y chat
        client.submit_orders(0, [{"kind": "create", "position": [1, 2]}])
        client.submit_hash(0, "deadbeef")
        client.send_chat("hola host")
        ev = _pump(
            server, host, clients,
            lambda c: _has(c[host], OrdersReceived)
            and _has(c[host], HashReceived)
            and _has(c[host], ChatReceived),
        )
        orders = next(e for e in ev[host] if isinstance(e, OrdersReceived))
        assert orders.player_id == 1 and orders.turn == 0
        assert orders.orders == [{"kind": "create", "position": [1, 2]}]
        digest = next(e for e in ev[host] if isinstance(e, HashReceived))
        assert digest.player_id == 1 and digest.digest == "deadbeef"
        chat = next(e for e in ev[host] if isinstance(e, ChatReceived))
        assert chat.name == "C1" and chat.text == "hola host"

        # host → clientes: el bundle del turno y el chat del host
        host.broadcast_turn_orders(0, {0: [], 1: [{"kind": "create", "position": [3, 4]}]})
        host.send_chat("hola rival")
        ev = _pump(
            server, host, clients,
            lambda c: _has(c[client], TurnReady) and _has(c[client], ChatReceived),
        )
        bundle = next(e for e in ev[client] if isinstance(e, TurnReady))
        assert bundle.turn == 0
        assert bundle.bundle == {0: [], 1: [{"kind": "create", "position": [3, 4]}]}
        host_chat = next(e for e in ev[client] if isinstance(e, ChatReceived))
        assert host_chat.name == "Host" and host_chat.text == "hola rival"
    finally:
        _close_all(server, host, clients)


def test_cancel_notifies_clients():
    server, host, clients = _make_group(2)
    client = clients[0]
    try:
        _reach_lobby(server, host, clients)
        _reach_playing(server, host, clients)
        host.cancel("el host canceló la partida")
        ev = _pump(server, host, clients, lambda c: _has(c[client], Disconnected))
        disc = next(e for e in ev[client] if isinstance(e, Disconnected))
        assert "canceló" in disc.reason
        assert client.state is SessionState.CLOSED
    finally:
        _close_all(server, host, clients)


def test_abrupt_disconnect_reported():
    server, host, clients = _make_group(2)
    client = clients[0]
    try:
        _reach_lobby(server, host, clients)
        _reach_playing(server, host, clients)
        client.connection.close()  # caída abrupta, sin Bye
        ev = _pump(server, host, clients, lambda c: _has(c[host], Disconnected))
        disc = next(e for e in ev[host] if isinstance(e, Disconnected))
        assert disc.player_id == 1  # el host sabe quién se cayó
        assert host.state is SessionState.CLOSED
    finally:
        _close_all(server, host, clients)
