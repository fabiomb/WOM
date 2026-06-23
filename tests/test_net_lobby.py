"""LobbyServer sobre loopback: handshake, roster, chat global y catálogo de
partidas (crear / unirse / salir / errores)."""

import time

from wom import __version__
from wom.net.lobby import (
    LobbyServer,
    MatchClosed,
    MatchMessage,
    MatchOpened,
    PlayerEntered,
    PlayerExited,
    SeatFreed,
    SeatTaken,
)
from wom.net.protocol import (
    PROTOCOL_VERSION,
    CreateMatch,
    Error,
    Join,
    JoinMatch,
    LeaveMatch,
    LobbyChat,
    LobbyState,
    MatchJoined,
    Ready,
    Welcome,
)
from wom.net.transport import Server, connect

CONFIG_HASH = "test-config-hash"


def _make_lobby(**kwargs):
    server = Server(host="127.0.0.1", port=0, max_clients=8)
    kwargs.setdefault("config_hash", CONFIG_HASH)
    lobby = LobbyServer(name="S", server=server, **kwargs)
    return server, lobby


def _join(server, name, *, config_hash=CONFIG_HASH, password=""):
    conn = connect("127.0.0.1", server.port, timeout=3.0)
    conn.send(
        Join(
            wom_version=__version__,
            protocol_version=PROTOCOL_VERSION,
            config_hash=config_hash,
            name=name,
            password=password,
        )
    )
    return conn


def _pump(lobby, conns, predicate, timeout=3.0):
    events: list = []
    inbox = {c: [] for c in conns}
    deadline = time.time() + timeout
    while time.time() < deadline:
        events.extend(lobby.update())
        for c in conns:
            inbox[c].extend(c.poll())
        if predicate(events, inbox):
            break
        time.sleep(0.005)
    return events, inbox


def _has(items, kind):
    return any(isinstance(e, kind) for e in items)


def _last(items, kind):
    return [e for e in items if isinstance(e, kind)][-1]


def _close(server, lobby, conns):
    lobby.shutdown()
    for c in conns:
        c.close()
    server.close()


# --- handshake -------------------------------------------------------------


def test_handshake_admite_y_emite_entered():
    server, lobby = _make_lobby()
    conn = _join(server, "Ana")
    try:
        ev, inbox = _pump(lobby, [conn], lambda e, i: _has(e, PlayerEntered) and _has(i[conn], Welcome))
        entered = _last(ev, PlayerEntered)
        assert entered.name == "Ana" and entered.lobby_id == 0
        welcome = _last(inbox[conn], Welcome)
        assert welcome.accepted and welcome.lobby_id == 0 and welcome.name == "S"
        assert lobby.player_count == 1
    finally:
        _close(server, lobby, [conn])


def test_handshake_rechaza_config_distinta():
    server, lobby = _make_lobby()
    conn = _join(server, "Ana", config_hash="otra")
    try:
        _, inbox = _pump(lobby, [conn], lambda e, i: _has(i[conn], Welcome))
        welcome = _last(inbox[conn], Welcome)
        assert not welcome.accepted and "config" in welcome.reason.lower()
        assert lobby.player_count == 0
    finally:
        _close(server, lobby, [conn])


def test_handshake_rechaza_password_incorrecta():
    server, lobby = _make_lobby(require_password=True, password="abrete")
    conn = _join(server, "Ana", password="mal")
    try:
        _, inbox = _pump(lobby, [conn], lambda e, i: _has(i[conn], Welcome))
        assert not _last(inbox[conn], Welcome).accepted
        assert lobby.player_count == 0
    finally:
        _close(server, lobby, [conn])


def test_handshake_password_correcta_entra():
    server, lobby = _make_lobby(require_password=True, password="abrete")
    conn = _join(server, "Ana", password="abrete")
    try:
        _, inbox = _pump(lobby, [conn], lambda e, i: _has(i[conn], Welcome))
        assert _last(inbox[conn], Welcome).accepted
    finally:
        _close(server, lobby, [conn])


def test_nombre_repetido_se_rechaza():
    server, lobby = _make_lobby()
    a = _join(server, "Ana")
    _pump(lobby, [a], lambda e, i: _has(e, PlayerEntered))
    b = _join(server, "Ana")
    try:
        _, inbox = _pump(lobby, [a, b], lambda e, i: _has(i[b], Welcome))
        assert not _last(inbox[b], Welcome).accepted
        assert lobby.player_count == 1
    finally:
        _close(server, lobby, [a, b])


def test_handshake_timeout_cierra_conexion_zombi():
    clock = [0.0]
    server, lobby = _make_lobby(handshake_timeout=10.0, time_fn=lambda: clock[0])
    conn = connect("127.0.0.1", server.port, timeout=3.0)  # nunca manda Join
    try:
        _pump(lobby, [conn], lambda e, i: False, timeout=0.3)  # registra el pendiente
        clock[0] = 11.0  # pasa el plazo
        _pump(lobby, [conn], lambda e, i: not conn.alive, timeout=2.0)
        assert not conn.alive
        assert lobby.player_count == 0
    finally:
        _close(server, lobby, [conn])


# --- chat global -----------------------------------------------------------


def test_chat_global_se_reparte_a_todos():
    server, lobby = _make_lobby()
    a = _join(server, "Ana")
    b = _join(server, "Beto")
    _pump(lobby, [a, b], lambda e, i: lobby.player_count == 2)
    a.send(LobbyChat(name="Ana", text="hola a todos", ts=1.0))
    try:
        _, inbox = _pump(
            lobby, [a, b],
            lambda e, i: _has(i[a], LobbyChat) and _has(i[b], LobbyChat),
        )
        for conn in (a, b):
            chat = _last(inbox[conn], LobbyChat)
            assert chat.name == "Ana" and chat.text == "hola a todos"
    finally:
        _close(server, lobby, [a, b])


# --- catálogo de partidas --------------------------------------------------


def _enter(server, lobby, conns_names):
    conns = [_join(server, n) for n in conns_names]
    _pump(lobby, conns, lambda e, i: lobby.player_count == len(conns_names))
    return conns


def test_crear_partida_la_publica_y_une_al_creador():
    server, lobby = _make_lobby()
    a, b = _enter(server, lobby, ["Ana", "Beto"])
    a.send(CreateMatch(name="Sala de Ana", max_players=2, map_source="random"))

    def _b_sees_match(_e, i):
        return any(isinstance(m, LobbyState) and m.matches for m in i[b])

    try:
        ev, inbox = _pump(
            lobby, [a, b],
            lambda e, i: _has(e, MatchOpened) and _has(i[a], MatchJoined) and _b_sees_match(e, i),
        )
        opened = _last(ev, MatchOpened)
        assert opened.creator_id == 0 and opened.spec.max_players == 2
        joined = _last(inbox[a], MatchJoined)
        assert joined.seat == 0 and joined.match_id == opened.match_id
        # El catálogo del LobbyState muestra la partida disponible.
        state = [m for m in inbox[b] if isinstance(m, LobbyState) and m.matches][-1]
        assert any(row[0] == opened.match_id and row[4] == "abierta" for row in state.matches)
        assert lobby.match_count == 1
    finally:
        _close(server, lobby, [a, b])


def test_unirse_ocupa_asiento_y_llena():
    server, lobby = _make_lobby()
    a, b, c = _enter(server, lobby, ["Ana", "Beto", "Caro"])
    a.send(CreateMatch(name="Sala", max_players=2, map_source="random"))
    ev, _ = _pump(lobby, [a, b, c], lambda e, i: _has(e, MatchOpened))
    mid = _last(ev, MatchOpened).match_id
    b.send(JoinMatch(match_id=mid))
    try:
        ev, inbox = _pump(
            lobby, [a, b, c],
            lambda e, i: _has(e, SeatTaken) and _has(i[b], MatchJoined),
        )
        joined = _last(inbox[b], MatchJoined)
        assert joined.seat == 1
        # Ahora está llena: Caro no puede entrar.
        c.send(JoinMatch(match_id=mid))
        _, inbox = _pump(lobby, [a, b, c], lambda e, i: _has(i[c], Error))
        assert _last(inbox[c], Error).code == "MATCH_FULL"
    finally:
        _close(server, lobby, [a, b, c])


def test_unirse_a_partida_inexistente_da_error():
    server, lobby = _make_lobby()
    (a,) = _enter(server, lobby, ["Ana"])
    a.send(JoinMatch(match_id=999))
    try:
        _, inbox = _pump(lobby, [a], lambda e, i: _has(i[a], Error))
        assert _last(inbox[a], Error).code == "MATCH_GONE"
    finally:
        _close(server, lobby, [a])


def test_crear_estando_en_partida_da_error():
    server, lobby = _make_lobby()
    (a,) = _enter(server, lobby, ["Ana"])
    a.send(CreateMatch(name="S1", max_players=2, map_source="random"))
    _pump(lobby, [a], lambda e, i: _has(e, MatchOpened))
    a.send(CreateMatch(name="S2", max_players=2, map_source="random"))
    try:
        _, inbox = _pump(lobby, [a], lambda e, i: _has(i[a], Error))
        assert _last(inbox[a], Error).code == "INVALID"
        assert lobby.match_count == 1
    finally:
        _close(server, lobby, [a])


def test_crear_con_cupo_invalido_da_error():
    server, lobby = _make_lobby()
    (a,) = _enter(server, lobby, ["Ana"])
    a.send(CreateMatch(name="S", max_players=9, map_source="random"))
    try:
        _, inbox = _pump(lobby, [a], lambda e, i: _has(i[a], Error))
        assert _last(inbox[a], Error).code == "INVALID"
        assert lobby.match_count == 0
    finally:
        _close(server, lobby, [a])


def test_salir_libera_asiento_y_recicla_partida_vacia():
    server, lobby = _make_lobby()
    (a,) = _enter(server, lobby, ["Ana"])
    a.send(CreateMatch(name="S", max_players=2, map_source="random"))
    ev, _ = _pump(lobby, [a], lambda e, i: _has(e, MatchOpened))
    mid = _last(ev, MatchOpened).match_id
    a.send(LeaveMatch())
    try:
        ev, _ = _pump(lobby, [a], lambda e, i: _has(e, MatchClosed))
        assert _has(ev, SeatFreed) and _last(ev, MatchClosed).match_id == mid
        assert lobby.match_count == 0
    finally:
        _close(server, lobby, [a])


def test_caida_de_jugador_emite_exited_y_libera_asiento():
    server, lobby = _make_lobby()
    a, b = _enter(server, lobby, ["Ana", "Beto"])
    a.send(CreateMatch(name="S", max_players=2, map_source="random"))
    ev, _ = _pump(lobby, [a, b], lambda e, i: _has(e, MatchOpened))
    mid = _last(ev, MatchOpened).match_id
    b.send(JoinMatch(match_id=mid))
    _pump(lobby, [a, b], lambda e, i: _has(e, SeatTaken))
    b.close()  # caída abrupta
    try:
        ev, _ = _pump(lobby, [a, b], lambda e, i: _has(e, PlayerExited))
        assert _has(ev, SeatFreed)
        assert lobby.player_count == 1
        assert lobby.seats_of(mid) == {0: 0}  # solo queda Ana en su asiento
    finally:
        _close(server, lobby, [a])


# --- enrutado de mensajes de partida ---------------------------------------


def test_mensaje_de_partida_se_enruta_como_matchmessage():
    server, lobby = _make_lobby()
    (a,) = _enter(server, lobby, ["Ana"])
    a.send(CreateMatch(name="S", max_players=2, map_source="random"))
    ev, _ = _pump(lobby, [a], lambda e, i: _has(e, MatchOpened))
    mid = _last(ev, MatchOpened).match_id
    a.send(Ready(ready=True))  # mensaje de PARTIDA, no de lobby
    try:
        ev, _ = _pump(lobby, [a], lambda e, i: _has(e, MatchMessage))
        mm = _last(ev, MatchMessage)
        assert mm.match_id == mid and mm.seat == 0 and isinstance(mm.message, Ready)
        assert mm.message.ready is True
    finally:
        _close(server, lobby, [a])
