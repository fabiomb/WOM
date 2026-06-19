"""Lockstep de punta a punta (sin pygame): N `NetGame` sobre loopback.

Cada nodo tiene su propia copia del juego y solo conoce las órdenes de SU
jugador (las decide una AI). El host junta todo y reparte el conjunto completo;
tras cada turno todos los juegos deben quedar idénticos y sin divergencia. Es
la prueba que valida el corazón del multiplayer.
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
from wom.net.protocol import GameSetup
from wom.net.session import ClientSession, HostSession, SessionState
from wom.net.transport import Server, connect

CONFIG_HASH = "lockstep-test"


def _new_game(seed: int, n_players: int = 2) -> Game:
    players = [Player(i, f"P{i}", is_ai=False) for i in range(n_players)]
    params = MapParams(22, 15, max(3, n_players), 4, seed, n_players=n_players)
    return Game.new(params, players, VictoryMode.TOTAL)


def _setup_provider(names):
    return GameSetup(state={}, rules={}, names=list(names))


def _playing_group(n_players: int = 2):
    """Devuelve (server, host_session, [client_sessions]) ya en PLAYING."""
    server = Server(host="127.0.0.1", port=0, max_clients=n_players - 1)
    conns = [connect("127.0.0.1", server.port, timeout=3.0) for _ in range(n_players - 1)]
    host = HostSession(n_players, "Host", _setup_provider, config_hash=CONFIG_HASH)
    clients = [
        ClientSession(conns[i], f"C{i + 1}", config_hash=CONFIG_HASH)
        for i in range(n_players - 1)
    ]
    fed: set = set()
    host.set_ready(True)
    deadline = time.time() + 3.0
    while time.time() < deadline:
        for conn in server.poll_connections():
            if conn not in fed:
                fed.add(conn)
                host.add_connection(conn)
        host.update()
        for c in clients:
            c.update()
            if c.state is SessionState.LOBBY and not c.local_ready:
                c.set_ready(True)
        if host.state is SessionState.PLAYING and all(
            c.state is SessionState.PLAYING for c in clients
        ):
            break
        time.sleep(0.005)
    assert host.state is SessionState.PLAYING
    assert all(c.state is SessionState.PLAYING for c in clients)
    return server, host, clients


def _close(server, host, clients):
    host.cancel()
    for c in clients:
        c.connection.close()
    server.close()


def _drive_turn(nets, ais) -> None:
    """Todos envían sus órdenes y se bombea hasta que TODOS resuelven el turno."""
    for net, ai in zip(nets, ais):
        net.submit_local_orders(ai.decide_orders(net.game))
    deadline = time.time() + 3.0
    turns0 = [n.game.turn for n in nets]
    while time.time() < deadline:
        for n in nets:
            n.update()
        if all(n.game.turn > t for n, t in zip(nets, turns0)):
            return
        time.sleep(0.005)
    raise AssertionError("el turno no se resolvió en todos los nodos")


def _nets(host_s, clients_s, games):
    """NetGame por nodo: el host (0) y un cliente por cada rival."""
    nets = [NetGame(host_s, games[0], human_id=0, is_host=True)]
    for i, client_s in enumerate(clients_s, start=1):
        nets.append(NetGame(client_s, games[i], human_id=i, is_host=False))
    return nets


def test_lockstep_two_players_stay_in_sync():
    games = [_new_game(1), _new_game(1)]
    assert games[0].to_dict() == games[1].to_dict()
    server, host_s, clients_s = _playing_group(2)
    nets = _nets(host_s, clients_s, games)
    ais = [AIPlayer(0, level="facil"), AIPlayer(1, level="facil")]
    try:
        for _ in range(60):
            _drive_turn(nets, ais)
            assert nets[0].game.to_dict() == nets[1].game.to_dict(), (
                f"divergieron en el turno {nets[0].game.turn}"
            )
            assert not any(n.desync for n in nets)
            if nets[0].phase.name == "ENDED":
                break
        assert nets[0].game.battles_fought > 0
        assert nets[0].game.battles_fought == nets[1].game.battles_fought
    finally:
        _close(server, host_s, clients_s)


def test_lockstep_four_players_stay_in_sync():
    games = [_new_game(3, n_players=4) for _ in range(4)]
    assert all(g.to_dict() == games[0].to_dict() for g in games)
    server, host_s, clients_s = _playing_group(4)
    nets = _nets(host_s, clients_s, games)
    ais = [AIPlayer(i, level="facil") for i in range(4)]
    try:
        for _ in range(40):
            _drive_turn(nets, ais)
            ref = nets[0].game.to_dict()
            for n in nets[1:]:
                assert n.game.to_dict() == ref, f"divergió en el turno {nets[0].game.turn}"
            assert not any(n.desync for n in nets)
            if nets[0].phase.name == "ENDED":
                break
    finally:
        _close(server, host_s, clients_s)


def test_consume_resolved_feeds_the_animation():
    games = [_new_game(1), _new_game(1)]
    server, host_s, clients_s = _playing_group(2)
    nets = _nets(host_s, clients_s, games)
    ais = [AIPlayer(0, level="facil"), AIPlayer(1, level="facil")]
    try:
        _drive_turn(nets, ais)
        resolved = nets[0].consume_resolved()
        assert resolved is not None
        result, pre_turn = resolved
        assert isinstance(result, VictoryResult)
        assert isinstance(pre_turn, list) and pre_turn
        assert nets[0].consume_resolved() is None  # se consume una sola vez
    finally:
        _close(server, host_s, clients_s)


def test_canonical_order_is_stable_regardless_of_input_order():
    a = [
        MoveOrder(army_id=5, path=((0, 0),)),
        CreateArmyOrder(position=(2, 3)),
        SplitArmyOrder(source_id=1, composition=(("soldado", 2),)),
        MergeArmyOrder(source_id=4, target_id=2),
        TransferTroopsOrder(source_id=7, target_id=3, composition=(("arquero", 1),)),
    ]
    assert canonical_order(a) == canonical_order(list(reversed(a)))
    kinds = [type(o).__name__ for o in canonical_order(a)]
    assert kinds.index("MoveOrder") == len(kinds) - 1


def test_resync_recovers_a_diverged_client():
    """Si el cliente diverge, el host detecta el mismatch de hash, le reenvía su
    estado autoritativo (StateSync) y el cliente lo adopta, volviendo a quedar
    idéntico."""
    games = [_new_game(1), _new_game(1)]
    server, host_s, clients_s = _playing_group(2)
    nets = _nets(host_s, clients_s, games)
    host_net, client_net = nets
    try:
        client_net.game.players[0].food += 999  # fuerza divergencia
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
        _close(server, host_s, clients_s)


def test_peer_orders_referencing_others_armies_are_dropped():
    """El validador del host descarta órdenes de un rival que toquen ejércitos
    que no son suyos."""
    games = [_new_game(1), _new_game(1)]
    server, host_s, clients_s = _playing_group(2)
    host_net = _nets(host_s, clients_s, games)[0]
    try:
        host_army = host_net.game.armies_of(0)[0]  # del host, no del cliente
        peer = [MoveOrder(army_id=host_army.id, path=((0, 0),))]
        assert host_net._validate_owned(peer, player_id=1) == []
    finally:
        _close(server, host_s, clients_s)
