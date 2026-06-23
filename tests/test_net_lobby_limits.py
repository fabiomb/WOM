"""Anti-DDOS del LobbyServer: topes de conexiones (total y por IP) y
rate-limit de mensajes por conexión."""

import time

from wom import __version__
from wom.net.lobby import LobbyServer
from wom.net.protocol import (
    PROTOCOL_VERSION,
    Error,
    Join,
    LobbyChat,
    Welcome,
)
from wom.net.transport import Server, connect

CH = "limits-test"


def _lobby(**kwargs):
    server = Server(host="127.0.0.1", port=0, max_clients=16)
    kwargs.setdefault("config_hash", CH)
    return server, LobbyServer(name="S", server=server, **kwargs)


def _join(server, name="J"):
    conn = connect("127.0.0.1", server.port, timeout=3.0)
    conn.send(Join(wom_version=__version__, protocol_version=PROTOCOL_VERSION, config_hash=CH, name=name))
    return conn


def _pump(lobby, conns, predicate, timeout=3.0):
    inbox = {c: [] for c in conns}
    deadline = time.time() + timeout
    while time.time() < deadline:
        lobby.update()
        for c in conns:
            inbox[c].extend(c.poll())
        if predicate(inbox):
            break
        time.sleep(0.005)
    return inbox


def _closed(conns):
    return sum(1 for c in conns if not c.alive)


def test_tope_total_de_conexiones():
    server, lobby = _lobby(max_connections=2)
    conns = [_join(server, f"P{i}") for i in range(3)]
    try:
        _pump(lobby, conns, lambda _i: lobby.player_count >= 2 and _closed(conns) >= 1)
        assert lobby.player_count == 2  # entran 2
        assert _closed(conns) == 1      # el 3º se rechaza
    finally:
        lobby.shutdown()
        for c in conns:
            c.close()
        server.close()


def test_tope_por_ip():
    # Loopback: las 3 conexiones comparten IP (127.0.0.1).
    server, lobby = _lobby(max_connections=50, max_connections_per_ip=2)
    conns = [_join(server, f"P{i}") for i in range(3)]
    try:
        inbox = _pump(lobby, conns, lambda _i: lobby.player_count >= 2 and _closed(conns) >= 1)
        assert lobby.player_count == 2
        assert _closed(conns) == 1
        # el rechazado nunca fue admitido (no recibió Welcome aceptado)
        rejected = next(c for c in conns if not c.alive)
        assert not any(isinstance(m, Welcome) and m.accepted for m in inbox[rejected])
    finally:
        lobby.shutdown()
        for c in conns:
            c.close()
        server.close()


def test_rate_limit_cierra_al_que_inunda():
    server, lobby = _lobby(msg_rate=5, msg_burst=10)
    conn = _join(server, "Flood")
    try:
        _pump(lobby, [conn], lambda _i: lobby.player_count == 1)
        for _ in range(60):  # inunda muy por encima del burst
            conn.send(LobbyChat(name="Flood", text="spam", ts=0.0))
        inbox = _pump(lobby, [conn], lambda _i: lobby.player_count == 0 or not conn.alive)
        assert lobby.player_count == 0
        assert any(isinstance(m, Error) and m.code == "RATE_LIMITED" for m in inbox[conn])
    finally:
        lobby.shutdown()
        conn.close()
        server.close()


def test_dentro_del_limite_no_molesta():
    """Un jugador que manda pocos mensajes no es expulsado."""
    server, lobby = _lobby(msg_rate=30, msg_burst=60)
    conn = _join(server, "Normal")
    try:
        _pump(lobby, [conn], lambda _i: lobby.player_count == 1)
        for _ in range(5):
            conn.send(LobbyChat(name="Normal", text="hola", ts=0.0))
        _pump(lobby, [conn], lambda _i: False, timeout=0.3)
        assert lobby.player_count == 1 and conn.alive  # sigue dentro
    finally:
        lobby.shutdown()
        conn.close()
        server.close()
