"""Tests de la resolución de batallas."""

import random

from wom.core.army import Army
from wom.core.battle import BattleOutcome, resolve_battle
from wom.core.config import load_game_config, load_unit_classes
from wom.core.worldmap import Fort, Terrain, WorldMap

CLASSES = load_unit_classes()


def _flat_world() -> WorldMap:
    tiles = [[Terrain.PLAINS] * 5 for _ in range(5)]
    return WorldMap(width=5, height=5, tiles=tiles)


def _battle_config(**overrides) -> dict:
    cfg = dict(load_game_config()["batalla"])
    cfg.update(overrides)
    return cfg


def _army(army_id: int, owner: int, pos, soldados: int) -> Army:
    return Army(id=army_id, owner=owner, position=pos, composition={"soldado": soldados})


def test_ejercito_muy_superior_gana():
    world = _flat_world()
    strong = _army(0, 0, (1, 1), 90)
    weak = _army(1, 1, (1, 2), 10)
    result = resolve_battle(
        strong, weak, world, CLASSES, _battle_config(sigma_aleatoriedad=0.0), random.Random(1)
    )
    assert result.outcome == BattleOutcome.ATTACKER_WINS
    assert sum(result.defender_losses.values()) > sum(result.attacker_losses.values())
    assert result.attacker_xp_delta < 0  # toda batalla cuesta XP
    assert result.defender_xp_delta < result.attacker_xp_delta


def test_toda_batalla_causa_bajas():
    world = _flat_world()
    a = _army(0, 0, (1, 1), 50)
    b = _army(1, 1, (1, 2), 50)
    result = resolve_battle(a, b, world, CLASSES, _battle_config(), random.Random(3))
    assert sum(result.attacker_losses.values()) >= 1
    assert sum(result.defender_losses.values()) >= 1


def test_retirada_penaliza_extra():
    world = _flat_world()
    cfg_base = _battle_config(sigma_aleatoriedad=0.0, penalidad_retirada_tropas=0.0)
    cfg_pen = _battle_config(sigma_aleatoriedad=0.0, penalidad_retirada_tropas=0.3)
    # atacante algo más débil => ATTACKER_RETREATS (con sigma 0 es determinista)
    attacker = _army(0, 0, (1, 1), 40)
    defender = _army(1, 1, (1, 2), 50)
    base = resolve_battle(attacker, defender, world, CLASSES, cfg_base, random.Random(1))
    pen = resolve_battle(attacker, defender, world, CLASSES, cfg_pen, random.Random(1))
    assert base.outcome == pen.outcome == BattleOutcome.ATTACKER_RETREATS
    assert sum(pen.attacker_losses.values()) > sum(base.attacker_losses.values())
    assert pen.attacker_xp_delta < pen.defender_xp_delta


def test_defensa_en_fuerte_ayuda():
    cfg = _battle_config(sigma_aleatoriedad=0.0)
    attacker = _army(0, 0, (1, 1), 60)
    defender_field = _army(1, 1, (1, 2), 45)
    world_field = _flat_world()
    world_fort = _flat_world()
    world_fort.forts.append(Fort(position=(1, 2), owner=1))
    in_field = resolve_battle(attacker, defender_field, world_field, CLASSES, cfg, random.Random(1))
    in_fort = resolve_battle(attacker, defender_field, world_fort, CLASSES, cfg, random.Random(1))
    # mismo enfrentamiento: el fuerte reduce las bajas del defensor
    assert sum(in_fort.defender_losses.values()) < sum(in_field.defender_losses.values())


def test_determinismo_con_mismo_rng():
    world = _flat_world()
    cfg = _battle_config()
    a1 = resolve_battle(_army(0, 0, (1, 1), 50), _army(1, 1, (1, 2), 50),
                        world, CLASSES, cfg, random.Random(7))
    a2 = resolve_battle(_army(0, 0, (1, 1), 50), _army(1, 1, (1, 2), 50),
                        world, CLASSES, cfg, random.Random(7))
    assert a1 == a2
