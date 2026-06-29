"""Unidades del `NetBattleDriver` (autoridad del zoom de batalla en red).

El driver corre la simulación táctica del lado autoridad y produce los mensajes
del protocolo (Offer/Begin/Snapshot/End). Acá se verifica el ciclo
voting→prep→fighting→done, la votación, el "listo", los snapshots y el
auto-resuelto por desconexión, sin pygame ni sockets.
"""

import pytest

from wom.core.army import Army
from wom.core.config import load_game_config, load_unit_classes
from wom.core.worldmap import Terrain, WorldMap
from wom.net.net_battle import (
    DONE,
    FIGHTING,
    PREP,
    VOTING,
    NetBattleDriver,
    decode_result,
    encode_result,
)
from wom.net.protocol import (
    BattleBegin,
    BattleEnd,
    BattleOffer,
    BattleSnapshot,
)


@pytest.fixture
def world():
    tiles = [[Terrain.PLAINS for _ in range(10)] for _ in range(8)]
    return WorldMap(10, 8, tiles)


@pytest.fixture
def cfg():
    return load_game_config()["batalla"]


@pytest.fixture
def classes():
    return load_unit_classes()


def _armies():
    a = Army(id=1, owner=0, position=(4, 4), composition={"soldado": 30})
    d = Army(id=2, owner=1, position=(5, 4), composition={"soldado": 20})
    return a, d


def _driver(world, classes, cfg, *, mode, humans):
    a, d = _armies()
    return NetBattleDriver(
        battle_id=0, attacker=a, defender=d, world=world, classes=classes,
        battle_config=cfg, mode=mode, human_owners=humans, seed=7,
    )


def test_always_emits_begin_and_runs_to_done(world, classes, cfg):
    drv = _driver(world, classes, cfg, mode="always", humans=[0, 1])
    out = drv.drain()
    assert any(isinstance(m, BattleBegin) for m in out)
    assert drv.phase is PREP
    # Ambos listos → arranca el combate sin esperar la cuenta.
    drv.apply_input(0, "ready")
    drv.apply_input(1, "ready")
    assert drv.phase is FIGHTING
    guard = 0
    while not drv.finished and guard < 20000:
        drv.tick(0.1)
        guard += 1
    assert drv.finished and not drv.auto
    assert drv.result is not None
    end = [m for m in drv.drain() if isinstance(m, BattleEnd)]
    assert end and end[-1].result  # lleva el BattleResult serializado


def test_agree_offer_then_consensus_begins(world, classes, cfg):
    drv = _driver(world, classes, cfg, mode="agree", humans=[0, 1])
    assert any(isinstance(m, BattleOffer) for m in drv.drain())
    assert drv.phase is VOTING
    drv.apply_vote(0, True)
    assert drv.phase is VOTING  # falta el otro
    drv.apply_vote(1, True)
    assert drv.phase is PREP
    assert any(isinstance(m, BattleBegin) for m in drv.drain())


def test_agree_decline_auto_resolves(world, classes, cfg):
    drv = _driver(world, classes, cfg, mode="agree", humans=[0, 1])
    drv.drain()
    drv.apply_vote(0, True)
    drv.apply_vote(1, False)  # un "no" → auto
    assert drv.finished and drv.auto and drv.result is None
    end = [m for m in drv.drain() if isinstance(m, BattleEnd)]
    assert end and end[-1].result == {}  # auto: sin resultado embebido


def test_vote_timeout_counts_as_decline(world, classes, cfg):
    drv = _driver(world, classes, cfg, mode="agree", humans=[0, 1])
    drv.drain()
    drv.apply_vote(0, True)  # el otro nunca vota
    drv.tick(30.0)  # supera VOTE_TIMEOUT
    assert drv.finished and drv.auto


def test_prep_countdown_starts_fight(world, classes, cfg):
    drv = _driver(world, classes, cfg, mode="always", humans=[0, 1])
    drv.drain()
    for _ in range(120):  # consume la cuenta (el dt se acota a 0.2 por tick)
        drv.tick(0.2)
    assert drv.phase is FIGHTING


def test_snapshots_are_emitted_during_fight(world, classes, cfg):
    drv = _driver(world, classes, cfg, mode="always", humans=[0, 1])
    drv.drain()
    drv.apply_input(0, "ready")
    drv.apply_input(1, "ready")
    snaps = 0
    for _ in range(20):
        drv.tick(0.1)
        snaps += sum(isinstance(m, BattleSnapshot) for m in drv.drain())
    assert snaps > 0


def test_mark_absent_auto_resolves(world, classes, cfg):
    drv = _driver(world, classes, cfg, mode="always", humans=[0, 1])
    drv.drain()
    drv.apply_input(0, "ready")
    drv.apply_input(1, "ready")
    drv.tick(0.1)
    drv.mark_absent(0)  # un participante se cae a mitad de combate
    assert drv.finished and drv.auto and drv.phase is DONE


def test_human_vs_ai_has_one_ai_side(world, classes, cfg):
    # Un solo humano: el otro bando lo conduce una TacticalAI.
    drv = _driver(world, classes, cfg, mode="always", humans=[0])
    assert len(drv._ais) == 1
    drv.apply_input(0, "ready")  # con un solo humano, su "listo" alcanza
    assert drv.phase is FIGHTING


def test_result_codec_round_trips(world, classes, cfg):
    drv = _driver(world, classes, cfg, mode="always", humans=[0, 1])
    drv.apply_input(0, "ready")
    drv.apply_input(1, "ready")
    guard = 0
    while not drv.finished and guard < 20000:
        drv.tick(0.1)
        guard += 1
    assert decode_result(encode_result(drv.result)) == drv.result


# --- interpolación del espejo del cliente (movimiento fluido) -------------

from wom.core.tactical import build_tactical_battle
from wom.net.lockstep import ClientBattle


def test_client_interpolation_is_smooth_and_monotonic(world, classes, cfg):
    """El espejo del cliente se acerca al objetivo autoritativo de a poco (sin
    los microsaltos de pintar el snapshot crudo)."""
    import random as _r
    a, d = _armies()
    battle = build_tactical_battle(a, d, world, classes, cfg, _r.Random(1))
    cb = ClientBattle(battle, 0, 1, 2, [0, 1])
    u = battle.units[0]
    # Primer snapshot: calza con el deploy (sin interpolar).
    cb.apply_snapshot(BattleSnapshot(0, "fighting", 0.0, [[u.id, 2.0, 2.0, 10.0, 0]], []))
    assert (u.x, u.y) == (2.0, 2.0)
    # Objetivo lejano: la posición dibujada avanza suave hacia él.
    cb.apply_snapshot(BattleSnapshot(0, "fighting", 0.0, [[u.id, 5.0, 2.0, 10.0, 0]], []))
    xs = []
    for _ in range(8):
        cb.interpolate(1 / 60)
        xs.append(u.x)
    assert 2.0 < xs[0] < 5.0  # no saltó directo al objetivo
    deltas = [xs[i + 1] - xs[i] for i in range(len(xs) - 1)]
    assert all(delta > 0 for delta in deltas)  # monotónico hacia el objetivo
    assert max(deltas) < 1.0  # ningún paso brusco (la distancia total es 3.0)
    assert xs[-1] <= 5.0
