"""GameServer de punta a punta: varias partidas a la vez sobre loopback y
aislamiento de fallos por partida."""

import time

from wom import __version__
from wom.ai.ai_player import AIPlayer
from wom.core.game import Game
from wom.net.config_fingerprint import config_fingerprint
from wom.net.lockstep import canonical_order
from wom.net.match import Phase
from wom.net.orders_codec import decode_orders, encode_orders
from wom.net.protocol import (
    PROTOCOL_VERSION,
    CreateMatch,
    GameSetup,
    Hash,
    Join,
    JoinMatch,
    MatchJoined,
    Orders,
    Ready,
    Start,
    StateSync,
    TurnOrders,
    Welcome,
)
from wom.net.state_hash import state_digest
from wom.net.transport import connect

from server.config import ServerConfig
from server.game_server import GameServer


class _Client:
    """Cliente mínimo de prueba: habla el protocolo del servidor y corre el
    lockstep como lo haría el juego real."""

    def __init__(self, port: int, name: str):
        self.conn = connect("127.0.0.1", port, timeout=3.0)
        self.name = name
        self.conn.send(
            Join(
                wom_version=__version__,
                protocol_version=PROTOCOL_VERSION,
                config_hash=config_fingerprint(),
                name=name,
            )
        )
        self.welcomed = False
        self.match_id = None
        self.seat = None
        self.game: Game | None = None
        self.started = False
        self.turn = 0

    def poll(self) -> None:
        for m in self.conn.poll():
            if isinstance(m, Welcome):
                self.welcomed = m.accepted
            elif isinstance(m, MatchJoined):
                self.match_id = m.match_id
                self.seat = m.seat
            elif isinstance(m, GameSetup):
                self.game = Game.from_dict(m.state)
                self.seat = m.human_id
                self.turn = self.game.turn
            elif isinstance(m, Start):
                self.started = True
            elif isinstance(m, TurnOrders):
                self._apply(m)
            elif isinstance(m, StateSync):
                self.game.load_state(m.state)
                self.turn = self.game.turn

    def _apply(self, to: TurnOrders) -> None:
        bundle = {int(p): decode_orders(od) for p, od in to.orders}
        combined = canonical_order([o for ods in bundle.values() for o in ods])
        self.game.run_turn(combined)
        self.turn = self.game.turn
        self.conn.send(Hash(turn=self.turn, digest=state_digest(self.game)))

    def create(self, **kw) -> None:
        self.conn.send(CreateMatch(**kw))

    def join(self, mid: int) -> None:
        self.conn.send(JoinMatch(match_id=mid))

    def ready(self) -> None:
        self.conn.send(Ready(ready=True))

    def submit(self, ai: AIPlayer) -> None:
        self.conn.send(Orders(turn=self.turn, orders=encode_orders(ai.decide_orders(self.game))))

    def close(self) -> None:
        self.conn.close()


def _pump(server: GameServer, clients, predicate, timeout: float = 5.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        server.tick()
        for c in clients:
            c.poll()
        if predicate():
            return True
        time.sleep(0.005)
    return False


def test_dos_partidas_simultaneas_e_independientes():
    server = GameServer(ServerConfig(host="127.0.0.1", port=0))
    a = _Client(server.port, "A")
    b = _Client(server.port, "B")
    c = _Client(server.port, "C")
    d = _Client(server.port, "D")
    clients = [a, b, c, d]
    try:
        assert _pump(server, clients, lambda: all(x.welcomed for x in clients))

        a.create(name="M1", max_players=2, map_source="random")
        c.create(name="M2", max_players=2, map_source="random")
        assert _pump(server, clients, lambda: a.match_id is not None and c.match_id is not None)
        m1, m2 = a.match_id, c.match_id
        assert m1 != m2

        b.join(m1)
        d.join(m2)
        assert _pump(server, clients, lambda: b.match_id == m1 and d.match_id == m2)
        assert _pump(server, clients, lambda: all(x.game is not None for x in clients))

        for x in clients:
            x.ready()
        assert _pump(server, clients, lambda: all(x.started for x in clients))
        assert len(server.runners) == 2

        ais = {x: AIPlayer(x.seat, "facil") for x in clients}
        for _ in range(3):
            t0 = {x: x.turn for x in clients}
            for x in clients:
                x.submit(ais[x])
            assert _pump(server, clients, lambda: all(x.turn > t0[x] for x in clients)), "no avanzaron"

        assert a.turn >= 3 and c.turn >= 3
        # Dentro de cada partida, los dos jugadores están en sync.
        assert a.game.to_dict() == b.game.to_dict()
        assert c.game.to_dict() == d.game.to_dict()
        # Las dos partidas son independientes (otro estado/mapa).
        assert a.game.to_dict() != c.game.to_dict()
    finally:
        for x in clients:
            x.close()
        server.transport.close()


def test_una_partida_que_falla_no_tumba_el_servidor():
    """Una excepción dentro de un MatchRunner cierra solo esa partida."""
    server = GameServer(ServerConfig(host="127.0.0.1", port=0))

    class _Boom:
        match_id = 1
        phase = Phase.PLAYING
        finished = False
        seats: dict = {}

        def update(self):
            raise RuntimeError("boom")

    server.runners[1] = _Boom()
    try:
        server.tick()  # no debe propagar la excepción
        assert 1 not in server.runners  # se cerró esa partida, el server sigue
    finally:
        server.transport.close()
