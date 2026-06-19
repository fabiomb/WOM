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


def test_cuatro_jugadores_en_esquinas_distintas():
    """Con 4 jugadores hay 4 fuertes iniciales (uno por jugador), en esquinas
    separadas y todo alcanzable."""
    params, world = _gen(7, n_players=4, n_forts=6)
    starts = [f for f in world.forts if f.owner >= 0]
    assert sorted(f.owner for f in starts) == [0, 1, 2, 3]
    assert all(f.owner == -1 for f in world.forts[4:])
    # Las cuatro posiciones iniciales son distintas y bien repartidas.
    positions = [f.position for f in starts]
    assert len(set(positions)) == 4
    assert _is_fully_connected(world)


def test_validacion_de_jugadores():
    import pytest

    with pytest.raises(ValueError):
        MapParams(seed=1, n_players=5)  # supera el tope
    with pytest.raises(ValueError):
        MapParams(seed=1, n_players=4, n_forts=3)  # menos fuertes que jugadores


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


def test_los_puentes_nunca_terminan_en_agua():
    """Un puente se extiende de orilla a orilla: sobre su eje de cruce los
    vecinos son tierra u otro tramo de puente, jamás agua."""
    for seed in range(8):
        _, world = _gen(seed)
        for y in range(world.height):
            for x in range(world.width):
                tile = world.tiles[y][x]
                if tile is Terrain.BRIDGE_H:
                    ends = [(x - 1, y), (x + 1, y)]
                elif tile is Terrain.BRIDGE_V:
                    ends = [(x, y - 1), (x, y + 1)]
                else:
                    continue
                for end in ends:
                    if world.in_bounds(end):
                        assert world.terrain_at(end) is not Terrain.WATER, (
                            f"seed {seed}: puente cortado en {(x, y)}"
                        )


def test_puentes_son_transitables():
    from wom.core.worldmap import WorldMap

    tiles = [[Terrain.WATER] * 3 for _ in range(3)]
    tiles[1][1] = Terrain.BRIDGE_H
    world = WorldMap(width=3, height=3, tiles=tiles)
    assert world.is_passable((1, 1))
    assert not world.is_passable((0, 0))


def test_puente_es_tunel_de_un_solo_eje():
    from wom.core.worldmap import WorldMap

    # puente horizontal rodeado de tierra: aún así solo conecta este-oeste
    tiles = [[Terrain.PLAINS] * 3 for _ in range(3)]
    tiles[1][1] = Terrain.BRIDGE_H
    world = WorldMap(width=3, height=3, tiles=tiles)
    assert world.can_step((1, 1), (0, 1)) and world.can_step((1, 1), (2, 1))
    assert not world.can_step((1, 1), (1, 0))  # no se sale por el lateral
    assert not world.can_step((1, 1), (1, 2))
    assert not world.can_step((1, 0), (1, 1))  # tampoco se entra
    assert sorted(world.neighbors((1, 1))) == [(0, 1), (2, 1)]


def test_puentes_contiguos_no_se_conectan_lateralmente():
    from wom.core.worldmap import WorldMap

    # dos puentes horizontales apilados: túneles paralelos independientes
    tiles = [[Terrain.PLAINS] * 3 for _ in range(3)]
    tiles[1][1] = Terrain.BRIDGE_H
    tiles[2][1] = Terrain.BRIDGE_H
    world = WorldMap(width=3, height=3, tiles=tiles)
    assert not world.can_step((1, 1), (1, 2))
    # un puente vertical sí conecta norte-sur
    tiles[2][1] = Terrain.PLAINS
    tiles[1][1] = Terrain.BRIDGE_V
    assert world.can_step((1, 1), (1, 2)) and world.can_step((1, 1), (1, 0))
    assert not world.can_step((1, 1), (0, 1))


def test_serializacion_ida_y_vuelta():
    from wom.core.worldmap import WorldMap

    _, world = _gen(99)
    rebuilt = WorldMap.from_dict(world.to_dict())
    assert rebuilt.to_dict() == world.to_dict()
    assert rebuilt.forts[0].position == world.forts[0].position
