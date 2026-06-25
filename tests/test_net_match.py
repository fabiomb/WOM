"""MatchRunner: autoridad player-less del lockstep (sala, lockstep en sync,
validación por dueño, IA de relleno, reconexión, resync)."""

from collections import defaultdict

from wom.ai.ai_player import AIPlayer
from wom.core.game import Game, Player
from wom.core.mapgen import MAP_SIZES, MapParams
from wom.core.orders import MoveOrder
from wom.core.victory import VictoryMode
from wom.core.worldmap import WorldMap
from wom.net.lobby import MatchSpec
from wom.net.lockstep import canonical_order
from wom.net.match import MatchRunner, Phase, default_game_builder
from wom.net.orders_codec import decode_orders, encode_orders
from wom.net.protocol import GameSetup, Hash, Orders, Ready, Start, StateSync, TurnOrders
from wom.persistence.scenario import ScenarioDoc


class FakeSink:
    """Registra los mensajes que el runner manda, por lobby_id."""

    def __init__(self):
        self.outbox = defaultdict(list)

    def send_to(self, lobby_id, message):
        self.outbox[lobby_id].append(message)
        return True

    def last(self, lobby_id, kind):
        msgs = [m for m in self.outbox[lobby_id] if isinstance(m, kind)]
        return msgs[-1] if msgs else None

    def of(self, lobby_id, kind):
        return [m for m in self.outbox[lobby_id] if isinstance(m, kind)]


LID = 100  # lobby_id de un asiento = LID + seat


def _start_match(seed=2, n=2, ai_factory=None):
    """Crea un MatchRunner ya en PLAYING con n asientos; devuelve (sink, runner,
    setups por asiento)."""
    sink = FakeSink()
    spec = MatchSpec(name="t", max_players=n, map_source="random")
    runner = MatchRunner(1, spec, sink, seed=seed, ai_factory=ai_factory)
    for s in range(n):
        runner.player_joined(s, LID + s, f"P{s}")
    setups = {s: sink.last(LID + s, GameSetup) for s in range(n)}
    for s in range(n):
        runner.handle(s, LID + s, Ready(ready=True))
    return sink, runner, setups


def _client_games(setups):
    return {s: Game.from_dict(setups[s].state) for s in setups}


def _turn_bundle(sink, turn):
    """El TurnOrders que el runner difundió para `turn` (decodificado)."""
    msgs = [m for m in sink.outbox[LID] if isinstance(m, TurnOrders) and m.turn == turn]
    to = msgs[-1]
    return {int(p): decode_orders(od) for p, od in to.orders}


def _drive(sink, runner, clients, ais, turn):
    """Cada cliente decide sus órdenes y se las manda al runner; luego todos
    corren el bundle autoritativo que el runner difundió."""
    for s, g in clients.items():
        runner.handle(s, LID + s, Orders(turn=turn, orders=encode_orders(ais[s].decide_orders(g))))
    runner.update()
    bundle = _turn_bundle(sink, turn)
    combined = canonical_order([o for ods in bundle.values() for o in ods])
    for g in clients.values():
        g.run_turn(combined)
    return combined


# --- sala -----------------------------------------------------------------


def test_sala_manda_setup_a_todos_y_arranca_con_todos_listos():
    sink = FakeSink()
    spec = MatchSpec(name="t", max_players=2, map_source="random")
    runner = MatchRunner(1, spec, sink, seed=2)
    runner.player_joined(0, 100, "Ana")
    assert runner.game is None and runner.phase is Phase.ROOM  # falta el 2º
    runner.player_joined(1, 101, "Beto")
    # Al llenarse, ambos reciben GameSetup con su human_id.
    assert sink.last(100, GameSetup).human_id == 0
    assert sink.last(101, GameSetup).human_id == 1
    assert sink.last(100, GameSetup).state == sink.last(101, GameSetup).state

    runner.handle(0, 100, Ready(ready=True))
    assert runner.phase is Phase.ROOM  # falta Beto
    assert sink.last(100, Start) is None
    runner.handle(1, 101, Ready(ready=True))
    assert runner.phase is Phase.PLAYING
    assert sink.last(100, Start) is not None and sink.last(101, Start) is not None


# --- lockstep en sync ------------------------------------------------------


def test_lockstep_dos_jugadores_en_sync():
    sink, runner, setups = _start_match(seed=4, n=2)
    clients = _client_games(setups)
    assert all(g.to_dict() == runner.game.to_dict() for g in clients.values())
    ais = {0: AIPlayer(0, "facil"), 1: AIPlayer(1, "facil")}
    for _ in range(60):
        turn = runner._turn
        _drive(sink, runner, clients, ais, turn)
        ref = runner.game.to_dict()
        assert all(g.to_dict() == ref for g in clients.values()), f"divergió en turno {turn}"
        if runner.finished:
            break
    assert runner.game.battles_fought > 0


def test_lockstep_cuatro_jugadores_en_sync():
    sink, runner, setups = _start_match(seed=3, n=4)
    clients = _client_games(setups)
    ais = {i: AIPlayer(i, "facil") for i in range(4)}
    for _ in range(40):
        turn = runner._turn
        _drive(sink, runner, clients, ais, turn)
        ref = runner.game.to_dict()
        assert all(g.to_dict() == ref for g in clients.values()), f"divergió en turno {turn}"
        if runner.finished:
            break


# --- validación por dueño --------------------------------------------------


def test_ordenes_de_un_rival_que_tocan_lo_ajeno_se_descartan():
    sink, runner, setups = _start_match(seed=2, n=2)
    enemy_army = runner.game.armies_of(0)[0]  # del asiento 0
    bad = [MoveOrder(army_id=enemy_army.id, path=((0, 0),))]
    assert runner._validate_owned(bad, seat=1) == []  # el asiento 1 no lo controla
    own_army = runner.game.armies_of(1)[0]
    good = [MoveOrder(army_id=own_army.id, path=((0, 0),))]
    assert runner._validate_owned(good, seat=1) == good


# --- IA de relleno ---------------------------------------------------------


def _ai_factory(seat):
    return AIPlayer(seat, "facil")


def test_ia_cubre_a_un_jugador_caido_y_sigue_en_sync():
    sink, runner, setups = _start_match(seed=5, n=3, ai_factory=_ai_factory)
    clients = _client_games(setups)
    ais = {i: AIPlayer(i, "facil") for i in range(3)}
    # un turno con los 3
    _drive(sink, runner, clients, ais, runner._turn)

    runner.player_left(2, LID + 2)  # se cae el asiento 2
    assert 2 in runner._absent
    assert any("IA" in text for _who, text in runner.chat_log)
    del clients[2]  # ese cliente ya no corre localmente

    for _ in range(5):
        turn = runner._turn
        # solo 0 y 1 mandan; la IA cubre al 2
        for s in (0, 1):
            runner.handle(s, LID + s, Orders(turn=turn, orders=encode_orders(ais[s].decide_orders(clients[s]))))
        runner.update()
        bundle = _turn_bundle(sink, turn)
        assert 2 in bundle  # la IA aportó las órdenes del ausente
        combined = canonical_order([o for ods in bundle.values() for o in ods])
        for g in clients.values():
            g.run_turn(combined)
        ref = runner.game.to_dict()
        assert all(g.to_dict() == ref for g in clients.values())
        if runner.finished:
            break


def test_jugador_reconecta_y_recibe_estado_vivo():
    sink, runner, setups = _start_match(seed=5, n=2, ai_factory=_ai_factory)
    clients = _client_games(setups)
    ais = {i: AIPlayer(i, "facil") for i in range(2)}
    _drive(sink, runner, clients, ais, runner._turn)
    runner.player_left(1, LID + 1)
    # avanza un par de turnos con la IA cubriendo al 1
    for _ in range(2):
        turn = runner._turn
        runner.handle(0, LID + 0, Orders(turn=turn, orders=encode_orders(ais[0].decide_orders(clients[0]))))
        runner.update()
    live = runner.game.to_dict()

    # Reconecta con un lobby_id nuevo: recibe el estado vivo + Start.
    runner.player_rejoined(1, 201, "P1-vuelve")
    assert 1 not in runner._absent
    setup = sink.last(201, GameSetup)
    assert setup is not None and setup.human_id == 1 and setup.state == live
    assert sink.last(201, Start) is not None


# --- resync (autoridad) ----------------------------------------------------


def test_hash_divergente_dispara_statesync():
    sink, runner, setups = _start_match(seed=5, n=2)
    clients = _client_games(setups)
    ais = {i: AIPlayer(i, "facil") for i in range(2)}
    _drive(sink, runner, clients, ais, runner._turn)
    resolved_turn = runner.game.turn  # ya avanzó

    # hash correcto del cliente 1 → sin resync
    good = runner._auth_hashes[resolved_turn]
    runner.handle(1, LID + 1, Hash(turn=resolved_turn, digest=good))
    assert sink.last(LID + 1, StateSync) is None
    # hash incorrecto → el servidor le reenvía su estado autoritativo
    runner.handle(1, LID + 1, Hash(turn=resolved_turn, digest="0" * 40))
    sync = sink.last(LID + 1, StateSync)
    assert sync is not None and sync.state == runner.game.to_dict()


# --- construcción desde escenario -----------------------------------------


def test_builder_respeta_el_tamano_de_mapa():
    spec = MatchSpec(name="t", max_players=2, map_source="random", rules={"map_size": "chico"})
    game = default_game_builder(spec, seed=1, names={0: "a", 1: "b"})
    w, h, _f, _t = MAP_SIZES["chico"]
    assert game.world.width == w and game.world.height == h
    # Sin map_size cae a 'medio'.
    game2 = default_game_builder(MatchSpec(name="t", max_players=2, map_source="random"), seed=1, names={})
    assert game2.world.width == MAP_SIZES["medio"][0]


def test_chat_de_sala_se_relaya_entre_jugadores():
    from wom.net.protocol import Chat

    sink = FakeSink()
    spec = MatchSpec(name="t", max_players=2, map_source="random")
    runner = MatchRunner(1, spec, sink, seed=2)
    runner.player_joined(0, 100, "P0")
    runner.player_joined(1, 101, "P1")  # sala llena (fase ROOM)
    runner.handle(0, 100, Chat(name="P0", text="hola sala", ts=0.0))
    assert any(m.text == "hola sala" for m in sink.of(101, Chat))  # le llega al otro
    assert not any(m.text == "hola sala" for m in sink.of(100, Chat))  # sin eco al emisor


def test_construye_la_partida_desde_un_escenario():
    base = Game.new(MapParams(20, 14, 3, 2, seed=1, n_players=2),
                    [Player(0, "a"), Player(1, "b")], VictoryMode.TOTAL)
    world = WorldMap.from_dict(base.world.to_dict())
    specs = [
        {"owner": a.owner, "position": list(a.position), "composition": dict(a.composition)}
        for a in base.armies
    ]
    doc = ScenarioDoc(world=world, players=[Player(0, ""), Player(1, "")],
                      army_specs=specs, victory_mode=VictoryMode.FLAGS)
    spec = MatchSpec(name="Esc", max_players=2, map_source="scenario", map_ref="x.wom")
    game = default_game_builder(spec, seed=9, names={0: "Ana", 1: "Beto"},
                                scenario_loader=lambda ref: doc)
    assert game.turn == 0
    assert game.world.to_dict() == world.to_dict()
    assert game.victory_mode is VictoryMode.FLAGS
    assert len(game.players) == 2 and game.players[0].name == "Ana"
