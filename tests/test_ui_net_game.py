"""GameScreen en modo red (MP4), headless con SDL dummy.

Verifica el flujo de un turno en red sobre un socket real (el rival lo simula
una `ClientSession` cruda) y que la reorganización del humano en red se difiere
como orden en vez de aplicarse al instante.
"""

import os
import time

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

import pygame
import pytest

from wom.core.game import Game, Player
from wom.core.mapgen import MapParams
from wom.core.orders import SplitArmyOrder, TransferTroopsOrder
from wom.core.victory import VictoryMode
from wom.net.lockstep import NetGame
from wom.net.protocol import GameSetup
from wom.net.session import ClientSession, HostSession, SessionState
from wom.net.transport import Server, connect
from wom.ui import theme
from wom.ui.game_screen import GameScreen


@pytest.fixture(scope="module")
def screen():
    pygame.init()
    yield pygame.display.set_mode(theme.WINDOW_SIZE)
    pygame.quit()


def _new_game(seed: int) -> Game:
    players = [Player(0, "Host"), Player(1, "Cliente")]
    return Game.new(MapParams(22, 15, 3, 4, seed), players, VictoryMode.TOTAL)


def _playing_sessions():
    server = Server(host="127.0.0.1", port=0)
    client_conn = connect("127.0.0.1", server.port, timeout=3.0)
    deadline = time.time() + 3.0
    while server.poll_connection() is None and time.time() < deadline:
        time.sleep(0.005)
    host = HostSession(
        server.poll_connection(),
        "Host",
        lambda name: GameSetup(state={}, rules={}, names=["Host", name]),
        config_hash="x",
    )
    client = ClientSession(client_conn, "Cliente", config_hash="x")
    host.set_ready(True)
    client_ready = False
    deadline = time.time() + 3.0
    while time.time() < deadline:
        host.update()
        client.update()
        if not client_ready and client.state is SessionState.LOBBY:
            client.set_ready(True)
            client_ready = True
        if host.state is SessionState.PLAYING and client.state is SessionState.PLAYING:
            break
        time.sleep(0.005)
    assert host.state is SessionState.PLAYING
    return server, host, client


def _free_neighbor(game, pos):
    return next(
        n for n in game.world.neighbors(pos)
        if game.army_at(n) is None and game.world.is_passable(n)
    )


def test_turno_en_red_se_resuelve_y_anima(screen):
    server, host_s, client_s = _playing_sessions()
    game = _new_game(1)
    net = NetGame(host_s, game, human_id=0, is_host=True)
    gs = GameScreen(game, human_id=0, net=net)
    try:
        army = game.armies_of(0)[0]
        target = _free_neighbor(game, army.position)
        gs.pending_paths[army.id] = [target]

        gs.end_turn()
        assert gs.waiting_peer  # órdenes enviadas, esperando al rival
        assert gs.notice == "Esperando al rival…"

        # Mientras espera, el input de órdenes está bloqueado (Enter no reenvía).
        gs.handle_event(pygame.event.Event(pygame.KEYDOWN, {"key": pygame.K_RETURN}))
        assert game.turn == 0 and gs.waiting_peer

        # El rival manda sus órdenes (vacías): alcanza para que el host resuelva.
        client_s.send_orders(0, [])
        deadline = time.time() + 3.0
        while game.turn == 0 and time.time() < deadline:
            gs.update()
            time.sleep(0.005)

        assert game.turn == 1
        assert not gs.waiting_peer
        assert gs.animation is not None  # el turno se anima localmente
        assert gs.notice is None
        gs.draw(screen)  # no debe romper
    finally:
        host_s.connection.close()
        client_s.connection.close()
        server.close()


def test_dos_gamescreens_humano_vs_humano(screen):
    """Partida real en red: dos GameScreen sobre loopback, cada uno con su
    propio humano. Tras un turno con órdenes de ambos, los dos juegos quedan
    idénticos y sincronizados."""
    server, host_s, client_s = _playing_sessions()
    host_game, client_game = _new_game(1), _new_game(1)
    host_net = NetGame(host_s, host_game, human_id=0, is_host=True)
    client_net = NetGame(client_s, client_game, human_id=1, is_host=False)
    host_gs = GameScreen(host_game, human_id=0, net=host_net)
    client_gs = GameScreen(client_game, human_id=1, net=client_net)
    try:
        for gs, game, pid in ((host_gs, host_game, 0), (client_gs, client_game, 1)):
            army = game.armies_of(pid)[0]
            gs.pending_paths[army.id] = [_free_neighbor(game, army.position)]
            gs.end_turn()
        assert host_gs.waiting_peer and client_gs.waiting_peer

        deadline = time.time() + 3.0
        while (host_game.turn == 0 or client_game.turn == 0) and time.time() < deadline:
            host_gs.update()
            client_gs.update()
            time.sleep(0.005)

        assert host_game.turn == 1 and client_game.turn == 1
        assert host_game.to_dict() == client_game.to_dict()  # lockstep en sync
        assert not host_net.desync and not client_net.desync
        host_gs.draw(screen)
        client_gs.draw(screen)
    finally:
        host_s.connection.close()
        client_s.connection.close()
        server.close()


def test_guardar_esta_deshabilitado_en_red(screen):
    server, host_s, client_s = _playing_sessions()
    game = _new_game(1)
    gs = GameScreen(game, human_id=0, net=NetGame(host_s, game, 0, is_host=True))
    try:
        gs.save()
        assert "No se puede guardar" in gs.notice
    finally:
        host_s.connection.close()
        client_s.connection.close()
        server.close()


class _FakeNet:
    """Net mínimo para probar la reorganización diferida sin sockets."""

    waiting = False
    disconnected = False
    desync = False

    def update(self):
        pass

    def consume_resolved(self):
        return None


def test_fusion_en_red_se_difiere_como_orden(screen):
    game = _new_game(1)
    gs = GameScreen(game, human_id=0, net=_FakeNet())
    a = game.armies_of(0)[0]
    a.composition = {"soldado": 20}
    adjacent = _free_neighbor(game, a.position)
    b = game.spawn_army(0, adjacent, {"soldado": 10})

    _click = lambda pos, mods=0: (
        pygame.key.set_mods(mods),
        gs.handle_event(
            pygame.event.Event(
                pygame.MOUSEBUTTONDOWN,
                {"pos": gs.renderer.tile_center(pos), "button": 1},
            )
        ),
        pygame.key.set_mods(0),
    )

    _click(a.position)  # selecciona a
    _click(b.position, pygame.KMOD_SHIFT)  # shift+click → modal de fusión
    assert gs.pending_merge == (a.id, b.id)
    gs.handle_event(pygame.event.Event(pygame.KEYDOWN, {"key": pygame.K_s}))

    # No se aplicó al instante: ambos ejércitos siguen como estaban...
    assert game.army_by_id(a.id) is not None and game.army_by_id(b.id) is not None
    assert a.composition == {"soldado": 20} and b.composition == {"soldado": 10}
    # ...y quedó encolada una orden de transferencia para el fin del turno.
    assert len(gs.pending_reorg) == 1
    order = gs.pending_reorg[0]
    assert isinstance(order, TransferTroopsOrder)
    assert order.source_id == a.id and order.target_id == b.id


def test_division_en_red_se_difiere_como_orden(screen):
    game = _new_game(1)
    gs = GameScreen(game, human_id=0, net=_FakeNet())
    army = game.armies_of(0)[0]
    army.composition = {"soldado": 8, "arquero": 4}
    gs.selected_id = army.id

    gs._open_split()
    assert gs.pending_split == army.id
    gs.split_picker.adjust("arquero", 2)
    gs.handle_event(pygame.event.Event(pygame.KEYDOWN, {"key": pygame.K_s}))

    # No se dividió en el acto; se encoló una SplitArmyOrder.
    assert army.composition == {"soldado": 8, "arquero": 4}
    assert len(gs.pending_reorg) == 1
    assert isinstance(gs.pending_reorg[0], SplitArmyOrder)
