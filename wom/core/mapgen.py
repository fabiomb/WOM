"""Generador aleatorio de mapas (v1; el editor de mapas llega después).

Garantías que cumple el generador:
- Los fuertes iniciales de los jugadores 0 y 1 quedan en bandas opuestas
  del mapa (izquierda/derecha).
- Todos los fuertes y pueblos son alcanzables entre sí (conectividad
  verificada por flood-fill sobre tiles transitables).
- Misma seed + mismos parámetros => mismo mapa (determinismo).

Si tras varios intentos no se logra un mapa conectado, los últimos intentos
se generan sin agua (siempre conectados).
"""

from __future__ import annotations

import random
from collections import deque
from dataclasses import dataclass

from wom.core.worldmap import Coord, Fort, Terrain, Town, WorldMap

# Pesos de distribución de terreno del generador v1.
TERRAIN_WEIGHTS = {
    Terrain.PLAINS: 0.55,
    Terrain.FOREST: 0.20,
    Terrain.MOUNTAIN: 0.15,
    Terrain.WATER: 0.10,
}

MAX_ATTEMPTS = 50
NO_WATER_AFTER = 40  # a partir de este intento se genera sin agua
MIN_FEATURE_DISTANCE = 3  # distancia Manhattan mínima entre forts/towns


@dataclass(frozen=True)
class MapParams:
    """Parámetros que setea el usuario al crear la partida."""

    width: int = 30
    height: int = 20
    n_forts: int = 4   # total, incluye el fuerte inicial de cada jugador
    n_towns: int = 6
    seed: int | None = None

    def __post_init__(self) -> None:
        if self.width < 10 or self.height < 10:
            raise ValueError("el mapa debe ser de al menos 10x10")
        if self.n_forts < 2:
            raise ValueError("se necesitan al menos 2 fuertes (uno por jugador)")


def generate_map(params: MapParams, rng: random.Random) -> WorldMap:
    """Genera un mapa aleatorio que cumple las garantías del módulo."""
    for attempt in range(MAX_ATTEMPTS):
        weights = dict(TERRAIN_WEIGHTS)
        if attempt >= NO_WATER_AFTER:
            weights[Terrain.WATER] = 0.0
        world = _random_terrain(params, weights, rng)
        if not _place_features(world, params, rng):
            continue
        if _is_fully_connected(world):
            return world
    raise RuntimeError("no se pudo generar un mapa conectado")  # pragma: no cover


def _random_terrain(
    params: MapParams, weights: dict[Terrain, float], rng: random.Random
) -> WorldMap:
    terrains = list(weights.keys())
    probs = list(weights.values())
    tiles = [
        rng.choices(terrains, weights=probs, k=params.width)
        for _ in range(params.height)
    ]
    return WorldMap(width=params.width, height=params.height, tiles=tiles)


def _place_features(world: WorldMap, params: MapParams, rng: random.Random) -> bool:
    """Coloca fuertes y pueblos; devuelve False si no encontró lugar."""
    taken: list[Coord] = []

    def pick(x_min: int, x_max: int) -> Coord | None:
        for _ in range(200):
            pos = (rng.randrange(x_min, x_max), rng.randrange(world.height))
            if all(_manhattan(pos, t) >= MIN_FEATURE_DISTANCE for t in taken):
                return pos
        return None

    band = max(2, world.width // 5)
    # Fuertes iniciales de los jugadores, en bandas opuestas.
    player_positions = [pick(0, band), pick(world.width - band, world.width)]
    if None in player_positions:
        return False
    for player_id, pos in enumerate(player_positions):
        taken.append(pos)
        world.forts.append(Fort(position=pos, owner=player_id))

    # Resto de fuertes (neutrales) y pueblos, en cualquier parte.
    for _ in range(params.n_forts - 2):
        pos = pick(0, world.width)
        if pos is None:
            return False
        taken.append(pos)
        world.forts.append(Fort(position=pos))
    for _ in range(params.n_towns):
        pos = pick(0, world.width)
        if pos is None:
            return False
        taken.append(pos)
        world.towns.append(Town(position=pos))

    # Los tiles con fuerte/pueblo siempre son transitables.
    for x, y in taken:
        world.tiles[y][x] = Terrain.PLAINS
    return True


def _is_fully_connected(world: WorldMap) -> bool:
    """Verifica por flood-fill que todos los forts/towns son alcanzables."""
    targets = {f.position for f in world.forts} | {t.position for t in world.towns}
    start = world.forts[0].position
    seen = {start}
    queue = deque([start])
    while queue:
        pos = queue.popleft()
        for neighbor in world.neighbors(pos):
            if neighbor not in seen:
                seen.add(neighbor)
                queue.append(neighbor)
    return targets <= seen


def _manhattan(a: Coord, b: Coord) -> int:
    return abs(a[0] - b[0]) + abs(a[1] - b[1])
