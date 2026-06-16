"""Transporte TCP sobre loopback (127.0.0.1): conexión, envío e ida y vuelta."""

import time

import pytest

from wom.net.protocol import Chat, Ping, Pong
from wom.net.transport import Connection, Server, connect


def _wait(predicate, timeout: float = 3.0, interval: float = 0.01) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


def _recv_n(conn: Connection, n: int, timeout: float = 3.0) -> list:
    deadline = time.time() + timeout
    out: list = []
    while time.time() < deadline and len(out) < n:
        out.extend(conn.poll())
        if len(out) < n:
            time.sleep(0.005)
    return out


def _connected_pair():
    server = Server(host="127.0.0.1", port=0)
    client = connect("127.0.0.1", server.port, timeout=3.0)
    assert _wait(lambda: server.poll_connection() is not None), "el host no aceptó"
    return server, server.poll_connection(), client


def test_server_picks_free_port_and_accepts():
    server, host_conn, client = _connected_pair()
    try:
        assert server.port > 0
        assert host_conn.alive and client.alive
    finally:
        client.close()
        host_conn.close()
        server.close()


def test_messages_flow_both_directions():
    server, host_conn, client = _connected_pair()
    try:
        client.send(Chat(name="Ana", text="hola", ts=1.0))
        assert _recv_n(host_conn, 1) == [Chat(name="Ana", text="hola", ts=1.0)]
        host_conn.send(Pong(t=2.0))
        assert _recv_n(client, 1) == [Pong(t=2.0)]
    finally:
        client.close()
        host_conn.close()
        server.close()


def test_multiple_messages_preserved_in_order():
    server, host_conn, client = _connected_pair()
    try:
        for i in range(5):
            client.send(Ping(t=float(i)))
        assert _recv_n(host_conn, 5) == [Ping(t=float(i)) for i in range(5)]
    finally:
        client.close()
        host_conn.close()
        server.close()


def test_peer_disconnect_marks_not_alive():
    server, host_conn, client = _connected_pair()
    try:
        client.close()
        assert _wait(lambda: not host_conn.alive), "el host no detectó la caída"
        assert host_conn.send(Ping(t=0.0)) is False
    finally:
        host_conn.close()
        server.close()


def test_connect_to_nowhere_raises():
    with pytest.raises(OSError):
        # Puerto cerrado: debe fallar rápido, no colgar.
        connect("127.0.0.1", 1, timeout=1.0)
