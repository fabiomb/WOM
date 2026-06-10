"""Generador aleatorio de mapas (v1; el editor de mapas llega después).

El terreno se genera por *features* coherentes en lugar de ruido tile a
tile: cadenas montañosas (caminatas con dirección dominante y serpenteo),
manchas de bosque (crecimiento desde semillas), lagos (manchas de agua) y
ríos serpenteantes que cruzan el mapa dejando vados transitables para no
cortar la conectividad.

Garantías que cumple el generador:
- Los fuertes iniciales de los jugadores 0 y 1 quedan en bandas opuestas
  del mapa (izquierda/derecha).
- Todos los fuertes y pueblos son alcanzables entre sí (conectividad
  verificada por flood-fill sobre tiles transitables).
- Misma seed + mismos parámetros => mismo mapa (determinismo).

Si tras varios intentos no se logra un mapa conectado, los últimos intentos
se generan sin agua (siempre conectados, porque montaña y bosque son
transitables).
"""

from __future__ import annotations

import random
from collections import deque
from dataclasses import dataclass

from wom.core.worldmap import Coord, Fort, Terrain, Town, WorldMap

# Proporción objetivo de cada terreno sobre el total de tiles (el resto
# queda como llanura).
TERRAIN_TARGETS = {
    Terrain.FOREST: 0.20,
    Terrain.MOUNTAIN: 0.15,
    Terrain.WATER: 0.10,
}

MAX_ATTEMPTS = 50
NO_WATER_AFTER = 40  # a partir de este intento se genera sin agua
MIN_FEATURE_DISTANCE = 3  # distancia Manhattan mínima entre forts/towns

# Direcciones (8) para las caminatas de cadenas montañosas; los giros se
# limitan a +-45 grados (índices vecinos) para que la cadena serpentee sin
# quebrarse.
_CHAIN_DIRECTIONS = [
    (1, 0), (1, 1), (0, 1), (-1, 1), (-1, 0), (-1, -1), (0, -1), (1, -1),
]


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
        world = _coherent_terrain(params, rng, with_water=attempt < NO_WATER_AFTER)
        if not _place_features(world, params, rng):
            continue
        if _is_fully_connected(world):
            return world
    raise RuntimeError("no se pudo generar un mapa conectado")  # pragma: no cover


def _coherent_terrain(
    params: MapParams, rng: random.Random, *, with_water: bool
) -> WorldMap:
    """Parte de pura llanura y pinta features hasta las proporciones objetivo."""
    tiles = [[Terrain.PLAINS] * params.width for _ in range(params.height)]
    world = WorldMap(width=params.width, height=params.height, tiles=tiles)
    area = params.width * params.height
    _paint_mountain_chains(world, int(area * TERRAIN_TARGETS[Terrain.MOUNTAIN]), rng)
    _paint_forest_blobs(world, int(area * TERRAIN_TARGETS[Terrain.FOREST]), rng)
    if with_water:
        _paint_water(world, int(area * TERRAIN_TARGETS[Terrain.WATER]), rng)
    return world


def _paint(world: WorldMap, pos: Coord, terrain: Terrain) -> int:
    """Pinta el tile solo si todavía es llanura; devuelve 1 si pintó."""
    x, y = pos
    if world.tiles[y][x] is Terrain.PLAINS:
        world.tiles[y][x] = terrain
        return 1
    return 0


def _paint_mountain_chains(world: WorldMap, target: int, rng: random.Random) -> None:
    """Cadenas montañosas: caminatas con dirección dominante, serpenteo
    ocasional (+-45 grados) y engrosamiento lateral aleatorio."""
    painted = 0
    for _ in range(200):
        if painted >= target:
            return
        x, y = rng.randrange(world.width), rng.randrange(world.height)
        direction = rng.randrange(len(_CHAIN_DIRECTIONS))
        length = rng.randint(4, 4 + max(world.width, world.height) // 3)
        for _ in range(length):
            if not world.in_bounds((x, y)):
                break
            painted += _paint(world, (x, y), Terrain.MOUNTAIN)
            dx, dy = _CHAIN_DIRECTIONS[direction]
            if rng.random() < 0.35:  # engrosar: tile perpendicular a la marcha
                side = (x - dy, y + dx)
                if world.in_bounds(side):
                    painted += _paint(world, side, Terrain.MOUNTAIN)
            if rng.random() < 0.3:  # serpentear
                direction = (direction + rng.choice((-1, 1))) % len(_CHAIN_DIRECTIONS)
                dx, dy = _CHAIN_DIRECTIONS[direction]
            x, y = x + dx, y + dy


def _paint_forest_blobs(world: WorldMap, target: int, rng: random.Random) -> None:
    """Bosques en manchas: semillas que crecen hacia tiles vecinos."""
    painted = 0
    for _ in range(200):
        if painted >= target:
            return
        seed = (rng.randrange(world.width), rng.randrange(world.height))
        size = min(rng.randint(5, 16), target - painted)
        painted += _grow_blob(world, seed, size, Terrain.FOREST, rng)


def _grow_blob(
    world: WorldMap, start: Coord, size: int, terrain: Terrain, rng: random.Random
) -> int:
    """Crece una mancha desde `start` tomando tiles de llanura adyacentes."""
    frontier = [start]
    painted = 0
    while frontier and painted < size:
        x, y = frontier.pop(rng.randrange(len(frontier)))
        if not _paint(world, (x, y), terrain):
            continue
        painted += 1
        for neighbor in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
            if world.in_bounds(neighbor) and rng.random() < 0.8:
                frontier.append(neighbor)
    return painted


def _paint_water(world: WorldMap, target: int, rng: random.Random) -> None:
    """Agua: un río que cruza el mapa (si es lo bastante grande) y lagos."""
    painted = 0
    if min(world.width, world.height) >= 15:
        painted += _paint_river(world, rng)
    for _ in range(100):
        if painted >= target:
            return
        seed = (rng.randrange(world.width), rng.randrange(world.height))
        size = min(rng.randint(3, 9), target - painted)
        painted += _grow_blob(world, seed, size, Terrain.WATER, rng)


def _paint_river(world: WorldMap, rng: random.Random) -> int:
    """Río serpenteante de borde a borde. Cada pocos tiles deja un vado
    (tile sin pintar) para que el río no corte el mapa en dos."""
    vertical = rng.random() < 0.5
    if vertical:
        x, y = rng.randrange(world.width // 4, 3 * world.width // 4), 0
        main = (0, 1)
    else:
        x, y = 0, rng.randrange(world.height // 4, 3 * world.height // 4)
        main = (1, 0)
    painted = 0
    next_ford = rng.randint(3, 6)
    for _ in range(3 * (world.height if vertical else world.width)):
        if not world.in_bounds((x, y)):
            break
        next_ford -= 1
        if next_ford <= 0:
            next_ford = rng.randint(3, 6)  # vado: este tile queda transitable
        elif world.tiles[y][x] is not Terrain.WATER:
            world.tiles[y][x] = Terrain.WATER  # el río atraviesa cualquier terreno
            painted += 1
        if rng.random() < 0.4:  # deriva lateral (meandro), sin salirse del mapa
            drift = rng.choice((-1, 1))
            if vertical:
                x = min(max(x + drift, 0), world.width - 1)
            else:
                y = min(max(y + drift, 0), world.height - 1)
        else:
            x, y = x + main[0], y + main[1]
    return painted


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
