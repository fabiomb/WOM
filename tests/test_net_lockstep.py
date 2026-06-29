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
from wom.net.lockstep import NetGame, Phase, canonical_order
from wom.net.protocol import GameSetup
from wom.net.session import ClientSession, GameReady, HostSession, SessionState
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
    games = [_new_game(2), _new_game(2)]  # seed con combate dentro de 60 turnos
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


def _ai_factory(player_id):
    return AIPlayer(player_id, level="facil")


def _advance(net, ai, others=()):
    """El host (net) y nodos activos `others` mandan órdenes y se resuelve un
    turno; el host cubre con IA a los jugadores ausentes. Devuelve False si la
    partida terminó."""
    nodes = [net, *(onet for onet, _ in others)]
    t0 = [n.game.turn for n in nodes]  # antes de enviar: sin clientes presentes,
    net.submit_local_orders(ai.decide_orders(net.game))  # el host resuelve ya
    for onet, oai in others:
        onet.submit_local_orders(oai.decide_orders(onet.game))
    deadline = time.time() + 3.0
    while time.time() < deadline:
        for n in nodes:
            n.update()
        if all(n.game.turn > t for n, t in zip(nodes, t0)):
            return net.phase is not Phase.ENDED
        time.sleep(0.005)
    raise AssertionError("el turno no se resolvió")


def test_ai_takes_over_dropped_player():
    """Si un cliente se cae, el host lo cubre con IA y la partida sigue: el host
    y el cliente que queda permanecen sincronizados."""
    games = [_new_game(5, n_players=3) for _ in range(3)]
    server, host_s, clients_s = _playing_group(3)
    host_net = NetGame(host_s, games[0], 0, is_host=True, ai_factory=_ai_factory)
    c1 = NetGame(clients_s[0], games[1], 1, is_host=False)
    c2 = NetGame(clients_s[1], games[2], 2, is_host=False)
    ai0, ai1, ai2 = AIPlayer(0, "facil"), AIPlayer(1, "facil"), AIPlayer(2, "facil")
    try:
        _drive_turn([host_net, c1, c2], [ai0, ai1, ai2])  # un turno con los 3
        clients_s[1].connection.close()  # se cae el jugador 2

        took_over = False
        for _ in range(6):
            alive = _advance(host_net, ai0, others=[(c1, ai1)])
            assert host_net.game.to_dict() == c1.game.to_dict()  # siguen en sync
            if 2 in host_net._absent:
                took_over = True
            if not alive:
                break
        assert took_over  # el host marcó al jugador 2 como ausente y lo cubrió
        assert any("IA" in text for _who, text in host_net.chat_log)
    finally:
        _close(server, host_s, clients_s)


def test_dropped_player_rejoins_with_live_state():
    """El jugador caído reconecta, recibe el estado vivo y retoma su lugar; el
    host deja de cubrirlo con IA y la partida sigue en sync."""
    games = [_new_game(7, n_players=2) for _ in range(2)]
    server, host_s, clients_s = _playing_group(2)
    host_net = NetGame(host_s, games[0], 0, is_host=True, ai_factory=_ai_factory)
    c1 = NetGame(clients_s[0], games[1], 1, is_host=False)
    ai0, ai1 = AIPlayer(0, "facil"), AIPlayer(1, "facil")
    rconn = None
    try:
        _drive_turn([host_net, c1], [ai0, ai1])
        clients_s[0].connection.close()  # se cae el jugador 1
        for _ in range(3):
            if not _advance(host_net, ai0):  # la IA cubre al 1
                break
        assert 1 in host_net._absent
        live_turn = host_net.game.turn

        # Reconecta: nueva conexión al mismo host.
        rconn = connect("127.0.0.1", server.port, timeout=3.0)
        rsession = ClientSession(rconn, "C1-vuelve", config_hash=CONFIG_HASH)
        setup = None
        deadline = time.time() + 3.0
        while time.time() < deadline:
            for sc in server.poll_connections():
                host_s.add_connection(sc)  # idempotente
            host_net.update()
            for ev in rsession.update():
                if isinstance(ev, GameReady):
                    setup = ev.setup
            if setup is not None and rsession.state is SessionState.PLAYING:
                break
            time.sleep(0.005)

        assert setup is not None and setup.human_id == 1
        assert 1 not in host_net._absent  # el host dejó de cubrirlo
        rejoined_game = Game.from_dict(setup.state)
        assert rejoined_game.turn == live_turn
        assert rejoined_game.to_dict() == host_net.game.to_dict()

        # Sigue jugando con el humano de vuelta, en sync.
        rnet = NetGame(rsession, rejoined_game, 1, is_host=False)
        if host_net.phase is not Phase.ENDED:
            _drive_turn([host_net, rnet], [ai0, AIPlayer(1, "facil")])
            assert host_net.game.to_dict() == rnet.game.to_dict()
    finally:
        _close(server, host_s, clients_s)
        if rconn is not None:
            rconn.close()


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


# --- zoom de batalla en red -----------------------------------------------
# Estas pruebas validan que la batalla dirigida en red (offer/vote/begin/
# snapshot/end) mantiene el lockstep: la autoridad simula y difunde, y todos
# aplican el MISMO BattleResult. El reloj del combate se inyecta (`_clock`) para
# no esperar en tiempo real.

from wom.core.worldmap import Fort, Terrain, WorldMap


def _duel_game() -> Game:
    """Mapa chico de pradera con un fuerte por jugador y dos ejércitos pegados;
    el jugador 0 puede entrar al tile del 1 y forzar una batalla en el turno 0."""
    tiles = [[Terrain.PLAINS for _ in range(10)] for _ in range(8)]
    forts = [Fort(position=(0, 0), owner=0), Fort(position=(9, 7), owner=1)]
    world = WorldMap(10, 8, tiles, forts=forts)
    players = [Player(0, "P0", is_ai=False), Player(1, "P1", is_ai=False)]
    specs = [
        {"owner": 0, "position": (4, 4), "composition": {"soldado": 30}},
        {"owner": 1, "position": (5, 4), "composition": {"soldado": 20}},
    ]
    return Game.from_setup(world, players, specs, VictoryMode.TOTAL, seed=1)


class _FakeClock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


def _battle_nets(mode: str):
    games = [_duel_game(), _duel_game()]
    assert games[0].to_dict() == games[1].to_dict()
    server, host_s, clients_s = _playing_group(2)
    host_net = NetGame(host_s, games[0], 0, is_host=True, tactical_mode=mode)
    client_net = NetGame(clients_s[0], games[1], 1, is_host=False, tactical_mode=mode)
    clock = _FakeClock()
    host_net._clock = clock
    client_net._clock = clock
    return server, host_s, clients_s, [host_net, client_net], clock


def _attack_orders(net: NetGame):
    """El jugador 0 marcha sobre el tile del jugador 1 (fuerza batalla)."""
    if net.human_id != 0:
        return []
    army = net.game.armies_of(0)[0]
    return [MoveOrder(army_id=army.id, path=((5, 4),))]


def _pump(nets, clock, *, dt=0.1, steps=4000, until=None):
    # Avanza el reloj (fake) para el dt del combate y deja respirar al socket de
    # loopback (entrega real) con una pausa mínima por iteración.
    for _ in range(steps):
        clock.t += dt
        for n in nets:
            n.update()
        if until is not None and until():
            return
        time.sleep(0.002)
    if until is not None:
        raise AssertionError("condición no alcanzada")


def test_net_battle_always_stays_in_sync():
    server, host_s, clients_s, nets, clock = _battle_nets("always")
    host_net, client_net = nets
    try:
        host_net.submit_local_orders(_attack_orders(host_net))
        client_net.submit_local_orders([])
        # Espera a que ambos abran la batalla dirigida.
        _pump(nets, clock, until=lambda: host_net.battle_present() and client_net.battle_present())
        assert host_net.battle_is_participant() and client_net.battle_is_participant()
        # Ambos marcan "listo" → arranca el combate antes del countdown.
        host_net.battle_ready()
        client_net.battle_ready()
        # Corre hasta que el turno se resuelve en ambos nodos.
        _pump(nets, clock, until=lambda: host_net.game.turn > 0 and client_net.game.turn > 0)
        assert host_net.game.to_dict() == client_net.game.to_dict()
        assert not host_net.desync and not client_net.desync
        assert host_net.game.battles_fought == 1
    finally:
        _close(server, host_s, clients_s)


def test_net_battle_agree_decline_auto_resolves():
    server, host_s, clients_s, nets, clock = _battle_nets("agree")
    host_net, client_net = nets
    try:
        host_net.submit_local_orders(_attack_orders(host_net))
        client_net.submit_local_orders([])
        _pump(nets, clock, until=lambda: host_net.battle_present() and client_net.battle_present())
        assert host_net.battle_phase() == "voting"
        # El host acepta, el cliente rechaza → se auto-resuelve.
        host_net.battle_vote(True)
        client_net.battle_vote(False)
        _pump(nets, clock, until=lambda: host_net.game.turn > 0 and client_net.game.turn > 0)
        assert host_net.game.to_dict() == client_net.game.to_dict()
        assert host_net.game.battles_fought == 1
    finally:
        _close(server, host_s, clients_s)


def test_net_battle_disconnect_auto_resolves():
    server, host_s, clients_s, nets, clock = _battle_nets("always")
    host_net, client_net = nets
    try:
        host_net.submit_local_orders(_attack_orders(host_net))
        client_net.submit_local_orders([])
        _pump(nets, clock, until=lambda: host_net.battle_present())
        # El cliente se cae en plena batalla: el host la auto-resuelve y sigue.
        clients_s[0].connection.close()
        _pump([host_net], clock, until=lambda: host_net.game.turn > 0)
        assert host_net.game.battles_fought == 1
        assert host_net.active_battle is None
    finally:
        _close(server, host_s, clients_s)
