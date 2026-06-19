"""Tests del modelo de combate táctico en tiempo real (wom/core/tactical.py)."""

import random

from wom.core.army import Army
from wom.core.battle import BattleOutcome
from wom.core.config import load_game_config, load_unit_classes
from wom.core.tactical import build_tactical_battle
from wom.core.worldmap import Fort, Terrain, WorldMap


def _world(width=10, height=8) -> WorldMap:
    tiles = [[Terrain.PLAINS] * width for _ in range(height)]
    return WorldMap(width=width, height=height, tiles=tiles)


def _classes():
    return load_unit_classes()


def _cfg():
    return load_game_config()["batalla"]


def _run(battle, dt=1 / 30, max_steps=10000):
    steps = 0
    while not battle.finished and steps < max_steps:
        battle.step(dt)
        steps += 1
    return battle


def test_atacante_superior_gana_en_campo_abierto():
    world = _world()
    attacker = Army(id=0, owner=0, position=(2, 2), composition={"soldado": 100})
    defender = Army(id=1, owner=1, position=(5, 2), composition={"soldado": 15})
    battle = build_tactical_battle(
        attacker, defender, world, _classes(), _cfg(), random.Random(1)
    )
    _run(battle)
    assert battle.finished
    assert battle.outcome() in (
        BattleOutcome.ATTACKER_WINS,
        BattleOutcome.DEFENDER_RETREATS,
    )
    result = battle.to_battle_result()
    # El defensor inferior sufre más bajas que el atacante.
    assert sum(result.defender_losses.values()) > sum(result.attacker_losses.values())


def test_resultado_determinista_con_mismo_seed():
    world = _world()

    def jugar():
        a = Army(id=0, owner=0, position=(2, 2), composition={"soldado": 40, "arquero": 20})
        d = Army(id=1, owner=1, position=(5, 2), composition={"caballero": 30, "soldado": 20})
        b = build_tactical_battle(a, d, world, _classes(), _cfg(), random.Random(7))
        _run(b)
        r = b.to_battle_result()
        return (r.outcome, r.attacker_losses, r.defender_losses)

    assert jugar() == jugar()


def test_bajas_no_superan_la_composicion():
    world = _world()
    attacker = Army(id=0, owner=0, position=(2, 2), composition={"caballero": 60})
    defender = Army(id=1, owner=1, position=(5, 2), composition={"arquero": 50})
    battle = build_tactical_battle(
        attacker, defender, world, _classes(), _cfg(), random.Random(3)
    )
    _run(battle)
    result = battle.to_battle_result()
    for cid, lost in result.attacker_losses.items():
        assert lost <= attacker.composition[cid]
    for cid, lost in result.defender_losses.items():
        assert lost <= defender.composition[cid]


def test_escenario_fuerte_arma_muralla_y_puerta():
    world = _world()
    world.forts.append(Fort(position=(5, 2), owner=1))
    attacker = Army(id=0, owner=0, position=(4, 2), composition={"soldado": 50})
    defender = Army(id=1, owner=1, position=(5, 2), composition={"soldado": 30})
    battle = build_tactical_battle(
        attacker, defender, world, _classes(), _cfg(), random.Random(5)
    )
    assert battle.defender_in_fort
    assert battle.walls  # hay muralla
    assert battle.gate_cells  # y una puerta
    # El defensor atrincherado nunca se marca como quebrado (pelea hasta morir).
    _run(battle)
    assert battle.defender_owner not in battle._broken


def test_defensor_en_fuerte_no_se_retira_en_el_resultado():
    world = _world()
    world.forts.append(Fort(position=(5, 2), owner=1))
    attacker = Army(id=0, owner=0, position=(4, 2), composition={"soldado": 100, "arquero": 40})
    defender = Army(id=1, owner=1, position=(5, 2), composition={"soldado": 10})
    battle = build_tactical_battle(
        attacker, defender, world, _classes(), _cfg(), random.Random(9)
    )
    _run(battle)
    # Atrincherado: o aguanta o lo destruyen, pero no figura como retirado.
    assert battle.outcome() != BattleOutcome.DEFENDER_RETREATS


def test_ordenes_de_grupo_se_asignan():
    world = _world()
    attacker = Army(id=0, owner=0, position=(2, 2), composition={"soldado": 20})
    defender = Army(id=1, owner=1, position=(5, 2), composition={"soldado": 20})
    battle = build_tactical_battle(
        attacker, defender, world, _classes(), _cfg(), random.Random(2)
    )
    mine = [u.id for u in battle.units if u.owner == 0]
    battle.command(mine, "hold")
    assert all(battle.unit_by_id(uid).order == ("hold",) for uid in mine)
    battle.command(mine, "move", (8.0, 4.0))
    assert all(battle.unit_by_id(uid).order[0] == "move" for uid in mine)
