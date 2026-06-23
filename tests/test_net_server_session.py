"""ServerSession contra un LobbyServer real en loopback: handshake, lobby,
chat, crear/unirse y errores."""

import time

from wom.net.lobby import LobbyServer
from wom.net.server_session import (
    Connected,
    ErrorReceived,
    LobbyUpdated,
    MatchJoinedEvent,
    Rejected,
    ServerSession,
    ServerState,
)
from wom.net.transport import Server, connect

CH = "session-test"


def _lobby():
    server = Server(host="127.0.0.1", port=0, max_clients=8)
    return server, LobbyServer(name="Srv", config_hash=CH, server=server)


def _session(server, name, *, config_hash=CH, password=""):
    conn = connect("127.0.0.1", server.port, timeout=3.0)
    return ServerSession(conn, name, password=password, config_hash=config_hash)


def _pump(lobby, sessions, predicate, timeout=3.0):
    events = {s: [] for s in sessions}
    deadline = time.time() + timeout
    while time.time() < deadline:
        lobby.update()
        for s in sessions:
            events[s].extend(s.update())
        if predicate(events):
            break
        time.sleep(0.005)
    return events


def _has(items, kind):
    return any(isinstance(e, kind) for e in items)


def _last(items, kind):
    return [e for e in items if isinstance(e, kind)][-1]


def test_handshake_entra_al_lobby():
    server, lobby = _lobby()
    s = _session(server, "Ana")
    try:
        ev = _pump(lobby, [s], lambda e: _has(e[s], Connected) and _has(e[s], LobbyUpdated))
        conn_ev = _last(ev[s], Connected)
        assert conn_ev.server_name == "Srv" and conn_ev.lobby_id == 0
        assert s.state is ServerState.LOBBY
        assert s.server_name == "Srv"
    finally:
        s.cancel()
        lobby.shutdown()
        server.close()


def test_handshake_rechazado_por_config():
    server, lobby = _lobby()
    s = _session(server, "Ana", config_hash="otra")
    try:
        ev = _pump(lobby, [s], lambda e: _has(e[s], Rejected))
        assert "config" in _last(ev[s], Rejected).reason.lower()
        assert s.state is ServerState.CLOSED
    finally:
        s.cancel()
        lobby.shutdown()
        server.close()


def test_crear_partida_recibe_matchjoined_y_catalogo():
    server, lobby = _lobby()
    s = _session(server, "Ana")
    _pump(lobby, [s], lambda e: s.state is ServerState.LOBBY)
    s.create_match(name="Mi sala", max_players=2, map_source="random")
    try:
        ev = _pump(lobby, [s], lambda e: _has(e[s], MatchJoinedEvent))
        mj = _last(ev[s], MatchJoinedEvent)
        assert mj.seat == 0 and mj.match_id == s.match_id
        # El catálogo del lobby refleja la partida.
        assert _pump(lobby, [s], lambda e: any(row for row in s.matches))
        assert any(row[1] == "Mi sala" for row in s.matches)
    finally:
        s.cancel()
        lobby.shutdown()
        server.close()


def test_unirse_a_partida_inexistente_da_error():
    server, lobby = _lobby()
    s = _session(server, "Ana")
    _pump(lobby, [s], lambda e: s.state is ServerState.LOBBY)
    s.join_match(999)
    try:
        ev = _pump(lobby, [s], lambda e: _has(e[s], ErrorReceived))
        assert _last(ev[s], ErrorReceived).code == "MATCH_GONE"
    finally:
        s.cancel()
        lobby.shutdown()
        server.close()


def test_chat_global_llega_a_otro_jugador():
    server, lobby = _lobby()
    a = _session(server, "Ana")
    b = _session(server, "Beto")
    _pump(lobby, [a, b], lambda e: a.state is ServerState.LOBBY and b.state is ServerState.LOBBY)
    a.send_lobby_chat("hola")
    try:
        from wom.net.server_session import LobbyChatReceived
        ev = _pump(lobby, [a, b], lambda e: _has(e[b], LobbyChatReceived))
        chat = _last(ev[b], LobbyChatReceived)
        assert chat.name == "Ana" and chat.text == "hola"
    finally:
        a.cancel()
        b.cancel()
        lobby.shutdown()
        server.close()
