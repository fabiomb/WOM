"""Integración LLM ↔ red: el `LLMPlayer` juega una partida en red por loopback.

Mismo harness que `test_net_lockstep`, pero el cliente (jugador 1) lo conduce un
`LLMPlayer` con un backend guionado (sin LLM real ni HTTP): emite órdenes válidas
contra el estado vivo, que viajan por el socket y se aplican en el lockstep. Prueba
el camino completo del cliente real (`Game.from_dict` del setup → `NetGame` →
órdenes del LLM por la red), y que la sincronía determinista se mantiene.
"""

import time

from wom.ai.ai_player import AIPlayer
from wom.core.game import Game, Player
from wom.core.mapgen import MapParams
from wom.core.victory import VictoryMode
from wom.llm.agent import LLMPlayer
from wom.llm.backend import LLMBackend
from wom.net.lockstep import NetGame, Phase
from wom.net.protocol import GameSetup
from wom.net.session import (
    ClientSession,
    GameReady,
    HostSession,
    SessionState,
    Started,
)
from wom.net.transport import Server, connect

CONFIG_HASH = "llm-net-test"


def _new_game(seed: int) -> Game:
    players = [Player(0, "Host"), Player(1, "LLM")]
    return Game.new(MapParams(20, 14, 3, 4, seed), players, VictoryMode.TOTAL)


class ScriptedBackend(LLMBackend):
    """Backend sin red: mueve el primer ejército propio a un vecino libre.

    Lee el estado vivo (no el texto) para emitir coordenadas reales: así prueba
    el ida y vuelta de órdenes por el socket sin depender de parsear el prompt.
    """

    def __init__(self, game_getter, player_id: int) -> None:
        self._game = game_getter
        self._pid = player_id
        self.calls = 0

    def complete(self, system: str, user: str) -> str:
        self.calls += 1
        game = self._game()
        mine = [a for a in game.armies if a.owner == self._pid]
        if not mine:
            return "[]"
        army = mine[0]
        frees = [n for n in game.world.neighbors(army.position) if game.army_at(n) is None]
        if not frees:
            return "[]"
        d = frees[0]
        return f'[{{"action": "move", "army": {army.id}, "to": [{d[0]}, {d[1]}]}}]'


def _connect_pair(host_game: Game):
    """Host + cliente por loopback hasta PLAYING; devuelve sesiones y el setup
    que recibió el cliente (con el estado inicial del juego)."""
    server = Server(host="127.0.0.1", port=0)
    client_conn = connect("127.0.0.1", server.port, timeout=3.0)
    deadline = time.time() + 3.0
    while server.poll_connection() is None and time.time() < deadline:
        time.sleep(0.005)

    def provider(_peer):
        return GameSetup(state=host_game.to_dict(), rules={}, names=["Host", "LLM"])

    host = HostSession(server.poll_connection(), "Host", provider, config_hash=CONFIG_HASH)
    client = ClientSession(client_conn, "LLM", config_hash=CONFIG_HASH)

    client_setup = None
    host.set_ready(True)
    client_ready = False
    deadline = time.time() + 3.0
    while time.time() < deadline:
        host.update()
        for ev in client.update():
            if isinstance(ev, GameReady):
                client_setup = ev.setup
            elif isinstance(ev, Started):
                pass
        if not client_ready and client.state is SessionState.LOBBY:
            client.set_ready(True)
            client_ready = True
        if host.state is SessionState.PLAYING and client.state is SessionState.PLAYING:
            break
        time.sleep(0.005)
    assert host.state is SessionState.PLAYING and client.state is SessionState.PLAYING
    assert client_setup is not None
    return server, host, client, client_setup


def _drive(host_net, client_net, host_player, llm_player):
    host_net.submit_local_orders(host_player.decide_orders(host_net.game))
    client_net.submit_local_orders(llm_player.decide_orders(client_net.game))
    deadline = time.time() + 3.0
    h0, c0 = host_net.game.turn, client_net.game.turn
    while time.time() < deadline:
        host_net.update()
        client_net.update()
        if host_net.game.turn > h0 and client_net.game.turn > c0:
            return
        time.sleep(0.005)
    raise AssertionError("el turno no se resolvió en ambos lados")


def test_llm_player_plays_a_networked_game_in_sync():
    host_game = _new_game(3)
    server, host_s, client_s, setup = _connect_pair(host_game)
    # El cliente reconstruye el juego desde el setup, igual que tools/llm_client.
    client_game = Game.from_dict(setup.state)
    assert host_game.to_dict() == client_game.to_dict()

    host_net = NetGame(host_s, host_game, human_id=0, is_host=True)
    client_net = NetGame(client_s, client_game, human_id=1, is_host=False)
    host_player = AIPlayer(0, level="facil")
    backend = ScriptedBackend(lambda: client_net.game, player_id=1)
    llm_player = LLMPlayer(1, backend)

    try:
        for _ in range(12):
            _drive(host_net, client_net, host_player, llm_player)
            assert host_net.game.to_dict() == client_net.game.to_dict(), (
                f"divergieron en el turno {host_net.game.turn}"
            )
            assert not host_net.desync and not client_net.desync
            if host_net.phase is Phase.ENDED:
                break
        assert backend.calls > 0, "el LLM decidió órdenes al menos una vez"
    finally:
        host_s.connection.close()
        client_s.connection.close()
        server.close()
