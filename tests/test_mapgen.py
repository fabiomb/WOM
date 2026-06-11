"""Tests del generador de mapas: determinismo, conectividad y garantías."""

import random
from collections import Counter

from wom.core.mapgen import TERRAIN_TARGETS, MapParams, _is_fully_connected, generate_map
from wom.core.worldmap import Terrain


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


def test_terreno_coherente_en_manchas():
    """Casi todo tile de bosque/montaña/agua tiene un vecino igual (con
    ruido tile a tile puro sería ~60-75%); mide la coherencia del generador."""
    for terrain in (Terrain.FOREST, Terrain.MOUNTAIN, Terrain.WATER):
        total = clustered = 0
        for seed in range(5):
            _, world = _gen(seed)
            for y in range(world.height):
                for x in range(world.width):
                    if world.tiles[y][x] is not terrain:
                        continue
                    total += 1
                    neighbors = [
                        (x + dx, y + dy)
                        for dx in (-1, 0, 1)
                        for dy in (-1, 0, 1)
                        if (dx, dy) != (0, 0)
                    ]
                    if any(
                        world.in_bounds(n) and world.tiles[n[1]][n[0]] is terrain
                        for n in neighbors
                    ):
                        clustered += 1
        assert total > 0, f"no se generó nada de {terrain}"
        assert clustered / total > 0.85, f"{terrain} demasiado disperso"


def test_proporciones_de_terreno():
    counts: Counter = Counter()
    for seed in range(5):
        _, world = _gen(seed)
        for row in world.tiles:
            counts.update(row)
    total = sum(counts.values())
    assert counts[Terrain.PLAINS] / total > 0.4  # la llanura domina
    for terrain, target in TERRAIN_TARGETS.items():
        share = counts[terrain] / total
        assert 0.3 * target < share < 1.8 * target, f"{terrain}: {share:.2f}"


def test_los_rios_tienen_puentes():
    bridges = 0
    for seed in range(5):
        _, world = _gen(seed)
        for row in world.tiles:
            for tile in row:
                if tile in (Terrain.BRIDGE_H, Terrain.BRIDGE_V):
                    bridges += 1
    assert bridges > 0  # los vados de los ríos ahora son puentes


def test_puentes_son_transitables():
    from wom.core.worldmap import WorldMap

    tiles = [[Terrain.WATER] * 3 for _ in range(3)]
    tiles[1][1] = Terrain.BRIDGE_H
    world = WorldMap(width=3, height=3, tiles=tiles)
    assert world.is_passable((1, 1))
    assert not world.is_passable((0, 0))


def test_serializacion_ida_y_vuelta():
    from wom.core.worldmap import WorldMap

    _, world = _gen(99)
    rebuilt = WorldMap.from_dict(world.to_dict())
    assert rebuilt.to_dict() == world.to_dict()
    assert rebuilt.forts[0].position == world.forts[0].position
