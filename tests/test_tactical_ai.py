"""Tests de la IA del combate táctico (wom/ai/tactical_ai.py)."""

import random

from wom.ai.tactical_ai import TacticalAI
from wom.core.army import Army
from wom.core.config import load_game_config, load_unit_classes
from wom.core.tactical import build_tactical_battle
from wom.core.worldmap import Terrain, WorldMap


def _world(width=12, height=8) -> WorldMap:
    tiles = [[Terrain.PLAINS] * width for _ in range(height)]
    return WorldMap(width=width, height=height, tiles=tiles)


def test_for_level_lee_parametros_de_ai_json():
    facil = TacticalAI.for_level(1, "facil")
    dificil = TacticalAI.for_level(1, "dificil")
    assert facil.prio_range == 0.0              # facil: no prioriza blancos
    assert dificil.prio_range > facil.prio_range  # dificil prioriza en radio amplio
    assert dificil.interval < facil.interval     # dificil reasigna más rápido


def test_dificil_le_gana_a_facil_con_fuerzas_iguales():
    """El nivel manda: con tropas iguales, la IA dificil (focus-fire amplio)
    debería terminar con más tropas que la facil (sin coordinación)."""
    world = _world()
    classes = load_unit_classes()
    cfg = load_game_config()["batalla"]
    dificil_total = facil_total = 0
    for seed in range(6):
        a = Army(id=0, owner=0, position=(3, 4), composition={"soldado": 30, "arquero": 15})
        d = Army(id=1, owner=1, position=(9, 4), composition={"soldado": 30, "arquero": 15})
        battle = build_tactical_battle(a, d, world, classes, cfg, random.Random(seed))
        ai_dificil = TacticalAI.for_level(0, "dificil")
        ai_facil = TacticalAI.for_level(1, "facil")
        steps = 0
        while not battle.finished and steps < 12000:
            ai_dificil.update(battle, 1 / 30)
            ai_facil.update(battle, 1 / 30)
            battle.step(1 / 30)
            steps += 1
        dificil_total += battle.side_troops(0)
        facil_total += battle.side_troops(1)
    assert dificil_total > facil_total


def test_plan_formation_dificil_elige_segun_composicion():
    world = _world()
    a = Army(id=0, owner=0, position=(2, 2), composition={"soldado": 40, "arquero": 20})
    d = Army(id=1, owner=1, position=(9, 2), composition={"soldado": 40, "arquero": 20})
    battle = build_tactical_battle(
        a, d, world, load_unit_classes(), load_game_config()["batalla"], random.Random(3)
    )
    # armas combinadas (infantería + arqueros) ⇒ formación clásica
    assert TacticalAI.for_level(1, "dificil").plan_formation(battle) == "clasica"
    assert battle.formation_of(1) == "clasica"


def test_plan_formation_facil_siempre_linea():
    world = _world()
    a = Army(id=0, owner=0, position=(2, 2), composition={"soldado": 40, "arquero": 20})
    d = Army(id=1, owner=1, position=(9, 2), composition={"soldado": 40, "arquero": 20})
    battle = build_tactical_battle(
        a, d, world, load_unit_classes(), load_game_config()["batalla"], random.Random(3)
    )
    assert TacticalAI.for_level(1, "facil").plan_formation(battle) == "linea"


def test_ia_asigna_ordenes_de_ataque_validas():
    world = _world()
    a = Army(id=0, owner=0, position=(2, 2), composition={"soldado": 30})
    d = Army(id=1, owner=1, position=(6, 2), composition={"soldado": 30})
    battle = build_tactical_battle(
        a, d, world, load_unit_classes(), load_game_config()["batalla"], random.Random(1)
    )
    ai = TacticalAI(owner=1)
    ai.update(battle, dt=1.0)
    enemy_ids = {u.id for u in battle.units if u.owner == 0}
    for u in battle.units:
        if u.owner == 1 and u.order is not None:
            assert u.order[0] == "attack"
            assert u.order[1] in enemy_ids


def test_la_ia_le_gana_a_un_bando_pasivo():
    """Con fuerzas iguales, el bando que conduce la IA (empuja y prioriza)
    debería vencer a uno que sólo aguanta la posición."""
    world = _world()
    classes = load_unit_classes()
    cfg = load_game_config()["batalla"]
    a = Army(id=0, owner=0, position=(2, 2), composition={"soldado": 40})
    d = Army(id=1, owner=1, position=(6, 2), composition={"soldado": 40})
    battle = build_tactical_battle(a, d, world, classes, cfg, random.Random(11))
    ai = TacticalAI(owner=1)
    # El bando 0 (pasivo) mantiene posición; el bando 1 lo conduce la IA.
    battle.command([u.id for u in battle.units if u.owner == 0], "hold")
    dt = 1 / 30
    steps = 0
    while not battle.finished and steps < 10000:
        ai.update(battle, dt)
        battle.step(dt)
        steps += 1
    assert battle.side_troops(1) >= battle.side_troops(0)
