"""Tests del generador de mapas: determinismo, conectividad y garantías."""

import random

from wom.core.mapgen import MapParams, _is_fully_connected, generate_map


def _gen(seed: int, **kwargs) -> tuple:
    params = MapParams(seed=seed, **kwargs)
    return params, generate_map(params, random.Random(seed))


def test_determinismo():
    _, world_a = _gen(123)
    _, world_b = _gen(123)
    assert world_a.to_dict() == world_b.to_dict()


def test_seeds_distintas_dan_mapas_distintos():
    _, world_a = _gen(1)
    _, world_b = _gen(2)
    assert world_a.to_dict() != world_b.to_dict()


def test_cantidades_y_duenios():
    params, world = _gen(42, n_forts=5, n_towns=7)
    assert len(world.forts) == 5
    assert len(world.towns) == 7
    assert world.forts[0].owner == 0
    assert world.forts[1].owner == 1
    assert all(f.owner == -1 for f in world.forts[2:])
    assert all(t.owner == -1 for t in world.towns)


def test_fuertes_iniciales_en_bandas_opuestas():
    params, world = _gen(7)
    band = max(2, params.width // 5)
    x0 = world.forts[0].position[0]
    x1 = world.forts[1].position[0]
    assert x0 < band
    assert x1 >= params.width - band


def test_todo_alcanzable():
    for seed in range(10):
        _, world = _gen(seed)
        assert _is_fully_connected(world)


def test_serializacion_ida_y_vuelta():
    from wom.core.worldmap import WorldMap

    _, world = _gen(99)
    rebuilt = WorldMap.from_dict(world.to_dict())
    assert rebuilt.to_dict() == world.to_dict()
    assert rebuilt.forts[0].position == world.forts[0].position
