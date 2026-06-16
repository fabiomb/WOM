"""Lockstep de punta a punta (sin pygame): dos `NetGame` sobre loopback.

Cada lado tiene su propia copia del juego y solo conoce las órdenes de SU
jugador (las decide una AI). El controlador intercambia órdenes y hashes; tras
cada turno ambos juegos deben quedar idénticos y sin desincronización. Es la
prueba que valida el corazón de MP4.
"""

import time

from wom.ai.ai_player import AIPlayer
from wom.core.game import Game, Player
from wom.core.mapgen import MapParams
from wom.core.orders import (
    CreateArmyOrder,
    MergeArmyOrder,
    MoveOrder,
    SplitArmyOrder,
    TransferTroopsOrder,
)
from wom.core.victory import VictoryMode, VictoryResult
from wom.net.lockstep import NetGame, canonical_order
from wom.net.session import ClientSession, HostSession, SessionState, Started
from wom.net.state_hash import state_digest
from wom.net.transport import Server, connect

CONFIG_HASH = "lockstep-test"


def _new_game(seed: int) -> Game:
    players = [
        Player(0, "Host", is_ai=False),
        Player(1, "Cliente", is_ai=False),
    ]
    return Game.new(MapParams(22, 15, 3, 4, seed), players, VictoryMode.TOTAL)


def _setup_provider(_peer_name: str):
    from wom.net.protocol import GameSetup

    return GameSetup(state={}, rules={}, names=["Host", "Cliente"])


def _playing_sessions():
    """Devuelve (server, host_session, client_session) ya en PLAYING."""
    server = Server(host="127.0.0.1", port=0)
    client_conn = connect("127.0.0.1", server.port, timeout=3.0)
    deadline = time.time() + 3.0
    while server.poll_connection() is None and time.time() < deadline:
        time.sleep(0.005)
    host = HostSession(
        server.poll_connection(), "Host", _setup_provider, config_hash=CONFIG_HASH
    )
    client = ClientSession(client_conn, "Cliente", config_hash=CONFIG_HASH)
    # Handshake + ambos listos → PLAYING.
    deadline = time.time() + 3.0
    host.set_ready(True)
    client_ready = False
    while time.time() < deadline:
        host.update()
        for ev in client.update():
            if isinstance(ev, Started):
                pass
        if not client_ready and client.state is SessionState.LOBBY:
            client.set_ready(True)
            client_ready = True
        if host.state is SessionState.PLAYING and client.state is SessionState.PLAYING:
            break
        time.sleep(0.005)
    assert host.state is SessionState.PLAYING and client.state is SessionState.PLAYING
    return server, host, client


def _drive_turn(host_net: NetGame, client_net: NetGame, ai0, ai1) -> None:
    """Ambos envían sus órdenes y se bombea hasta que los dos resuelven el turno.

    Hay que esperar a que AMBOS avancen, incluso en el último turno: cuando un
    lado resuelve y la partida termina, el otro todavía tiene que procesar las
    órdenes recibidas para resolver ese mismo turno (en la app real cada cliente
    bombea la red cada frame, así que siempre lo hace)."""
    host_net.submit_local_orders(ai0.decide_orders(host_net.game))
    client_net.submit_local_orders(ai1.decide_orders(client_net.game))
    deadline = time.time() + 3.0
    h0, c0 = host_net.game.turn, client_net.game.turn
    while time.time() < deadline:
        host_net.update()
        client_net.update()
        if host_net.game.turn > h0 and client_net.game.turn > c0:
            return
        time.sleep(0.005)
    raise AssertionError("el turno no se resolvió en ambos lados")


def test_lockstep_two_players_stay_in_sync():
    seed = 1
    host_game = _new_game(seed)
    client_game = _new_game(seed)
    assert host_game.to_dict() == client_game.to_dict()

    server, host_s, client_s = _playing_sessions()
    host_net = NetGame(host_s, host_game, human_id=0, is_host=True)
    client_net = NetGame(client_s, client_game, human_id=1, is_host=False)
    ai0 = AIPlayer(0, level="facil")
    ai1 = AIPlayer(1, level="facil")

    try:
        for _ in range(60):
            _drive_turn(host_net, client_net, ai0, ai1)
            assert host_net.game.to_dict() == client_net.game.to_dict(), (
                f"divergieron en el turno {host_net.game.turn}"
            )
            assert not host_net.desync and not client_net.desync
            if host_net.phase.name == "ENDED":
                break
        # Hubo combate (camino con RNG) y ambos terminaron iguales.
        assert host_net.game.battles_fought > 0
        assert host_net.game.battles_fought == client_net.game.battles_fought
    finally:
        host_s.connection.close()
        client_s.connection.close()
        server.close()


def test_consume_resolved_feeds_the_animation():
    """Tras resolver, cada lado expone (resultado, snapshot) una sola vez."""
    server, host_s, client_s = _playing_sessions()
    host_net = NetGame(host_s, _new_game(1), human_id=0, is_host=True)
    client_net = NetGame(client_s, _new_game(1), human_id=1, is_host=False)
    ai0, ai1 = AIPlayer(0, level="facil"), AIPlayer(1, level="facil")
    try:
        _drive_turn(host_net, client_net, ai0, ai1)
        resolved = host_net.consume_resolved()
        assert resolved is not None
        result, pre_turn = resolved
        assert isinstance(result, VictoryResult)
        assert isinstance(pre_turn, list) and pre_turn  # snapshot pre-turno
        assert host_net.consume_resolved() is None  # se consume una sola vez
    finally:
        host_s.connection.close()
        client_s.connection.close()
        server.close()


def test_canonical_order_is_stable_regardless_of_input_order():
    a = [
        MoveOrder(army_id=5, path=((0, 0),)),
        CreateArmyOrder(position=(2, 3)),
        SplitArmyOrder(source_id=1, composition=(("soldado", 2),)),
        MergeArmyOrder(source_id=4, target_id=2),
        TransferTroopsOrder(source_id=7, target_id=3, composition=(("arquero", 1),)),
    ]
    assert canonical_order(a) == canonical_order(list(reversed(a)))
    # Las reorganizaciones quedan antes que los movimientos.
    kinds = [type(o).__name__ for o in canonical_order(a)]
    assert kinds.index("MoveOrder") == len(kinds) - 1


def test_resync_recovers_a_diverged_client():
    """Si el cliente diverge (bug de determinismo), el host detecta el mismatch
    de hash, le reenvía su estado autoritativo (StateSync) y el cliente lo
    adopta, volviendo a quedar idéntico."""
    server, host_s, client_s = _playing_sessions()
    host_net = NetGame(host_s, _new_game(1), human_id=0, is_host=True)
    client_net = NetGame(client_s, _new_game(1), human_id=1, is_host=False)
    try:
        # Fuerza una divergencia antes de resolver el turno.
        client_net.game.players[0].food += 999
        assert host_net.game.to_dict() != client_net.game.to_dict()

        host_net.submit_local_orders([])
        client_net.submit_local_orders([])
        deadline = time.time() + 3.0
        while not client_net.resynced and time.time() < deadline:
            host_net.update()
            client_net.update()
            time.sleep(0.005)

        assert host_net.desync  # el host detectó el mismatch
        assert client_net.resynced  # el cliente adoptó el estado del host
        assert client_net.game.to_dict() == host_net.game.to_dict()
    finally:
        host_s.connection.close()
        client_s.connection.close()
        server.close()


def test_peer_orders_referencing_own_armies_are_dropped():
    """El validador descarta órdenes del par que toquen ejércitos propios."""
    server, host_s, client_s = _playing_sessions()
    host_net = NetGame(host_s, _new_game(1), human_id=0, is_host=True)
    try:
        own_army = host_net.game.armies_of(0)[0]  # del host (no del par)
        peer = [MoveOrder(army_id=own_army.id, path=((0, 0),))]
        assert host_net._validate_peer(peer) == []
    finally:
        host_s.connection.close()
        client_s.connection.close()
        server.close()
