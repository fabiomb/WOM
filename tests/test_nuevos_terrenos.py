"""Tests de los tiles nuevos: bosque ralo, colina y pantano.

Cubren las tres características pedidas:
- movimiento (costo de terreno, incluido el costo por clase del pantano),
- combate (bonus de terreno por clase),
- generación (cantidad, adyacencia del pantano al agua, determinismo).
"""

import random
from collections import Counter

from wom.core.config import load_game_config, load_unit_classes
from wom.core.mapgen import MapParams, _is_fully_connected, generate_map
from wom.core.pathfind import army_terrain_costs
from wom.core.worldmap import CHAR_TO_TERRAIN, TERRAIN_TO_CHAR, Terrain, WorldMap

_ORTHO = ((1, 0), (-1, 0), (0, 1), (0, -1))


# --- modelo y serialización -------------------------------------------------

def test_nuevos_terrenos_son_transitables():
    for t in (Terrain.FOREST_LIGHT, Terrain.MOUNTAIN_LIGHT, Terrain.MARSH):
        world = WorldMap(width=3, height=3, tiles=[[t] * 3 for _ in range(3)])
        assert world.is_passable((1, 1))


def test_chars_unicos_y_redondean():
    chars = list(TERRAIN_TO_CHAR.values())
    assert len(chars) == len(set(chars))  # cada terreno tiene su letra única
    for t in (Terrain.FOREST_LIGHT, Terrain.MOUNTAIN_LIGHT, Terrain.MARSH):
        assert CHAR_TO_TERRAIN[TERRAIN_TO_CHAR[t]] is t


# --- movimiento -------------------------------------------------------------

def test_costos_de_movimiento_configurados():
    costs = load_game_config()["costo_terreno"]
    assert costs["forest-less"] == 1  # bosque ralo: sin penalidad
    assert costs["mountain-less"] == 2  # colina: leve limitación
    assert costs["marshes"] == 3  # pantano: caro


def test_pantano_solo_penaliza_a_los_no_partisanos():
    cfg = load_game_config()
    base = cfg["costo_terreno"]
    overrides = cfg["costo_terreno_clase"]
    puro = army_terrain_costs(base, {"partisano": 10}, overrides)
    mixto = army_terrain_costs(base, {"partisano": 5, "soldado": 5}, overrides)
    otro = army_terrain_costs(base, {"soldado": 5}, overrides)
    assert puro["marshes"] == 1  # un ejército de puros partisanos cruza fácil
    assert mixto["marshes"] == base["marshes"]  # uno mixto paga la penalidad
    assert otro["marshes"] == base["marshes"]
    # Sin overrides devuelve el dict base tal cual (mismo objeto).
    assert army_terrain_costs(base, {"partisano": 1}) is base


# --- combate ----------------------------------------------------------------

def test_bonus_de_combate_de_los_nuevos_terrenos():
    classes = load_unit_classes()
    # Bosque ralo: estorba a caballero y arquero (penalidad), cubre al partisano.
    assert classes["caballero"].bonus_terreno["forest-less"] < 1.0
    assert classes["arquero"].bonus_terreno["forest-less"] < 1.0
    assert classes["partisano"].bonus_terreno["forest-less"] > 1.0
    # Colina: ventaja solo para arquero y partisano.
    assert classes["arquero"].bonus_terreno["mountain-less"] > 1.0
    assert classes["partisano"].bonus_terreno["mountain-less"] > 1.0
    assert classes["caballero"].bonus_terreno["mountain-less"] < 1.0
    assert "mountain-less" not in classes["soldado"].bonus_terreno  # neutral
    # Pantano: el partisano pelea mejor; el resto, peor o neutral.
    assert classes["partisano"].bonus_terreno["marshes"] > 1.0
    assert classes["caballero"].bonus_terreno["marshes"] < 1.0


# --- generación -------------------------------------------------------------

def _maps(n: int):
    for seed in range(n):
        yield generate_map(MapParams(seed=seed), random.Random(seed))


def test_se_generan_los_tres_tiles():
    presence = Counter()
    for world in _maps(6):
        seen = {t for row in world.tiles for t in row}
        for t in (Terrain.FOREST_LIGHT, Terrain.MOUNTAIN_LIGHT, Terrain.MARSH):
            if t in seen:
                presence[t] += 1
    for t in (Terrain.FOREST_LIGHT, Terrain.MOUNTAIN_LIGHT, Terrain.MARSH):
        assert presence[t] >= 5, f"{t} apenas aparece ({presence[t]}/6)"


def test_pantano_siempre_junto_al_agua():
    for world in _maps(10):
        for y in range(world.height):
            for x in range(world.width):
                if world.tiles[y][x] is not Terrain.MARSH:
                    continue
                assert any(
                    world.in_bounds((x + dx, y + dy))
                    and world.tiles[y + dy][x + dx] is Terrain.WATER
                    for dx, dy in _ORTHO
                ), f"pantano en {(x, y)} sin agua adyacente"


def test_nuevos_tiles_no_rompen_conectividad_ni_determinismo():
    for seed in range(5):
        a = generate_map(MapParams(seed=seed), random.Random(seed))
        b = generate_map(MapParams(seed=seed), random.Random(seed))
        assert a.to_dict() == b.to_dict()
        assert _is_fully_connected(a)
