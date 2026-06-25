"""Generador aleatorio de mapas (v1; el editor de mapas llega después).

El terreno se genera por *features* coherentes en lugar de ruido tile a
tile: cadenas montañosas (caminatas con dirección dominante y serpenteo),
manchas de bosque (crecimiento desde semillas), lagos (manchas de agua) y
ríos serpenteantes que cruzan el mapa dejando vados transitables para no
cortar la conectividad. Sobre esa base se tallan en la llanura las variantes
"livianas" y el pantano: bosque ralo (forest-less) en el borde de los bosques
y suelto, colinas (mountain-less) alrededor de las montañas y ocasionales, y
pantano (marshes) solo en la llanura pegada al agua.

Garantías que cumple el generador:
- Cada jugador arranca con su fuerte inicial en una zona alejada del resto
  (2 jugadores: bandas izquierda/derecha; 3-4: esquinas distintas).
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

from wom.core.worldmap import MAX_PLAYERS, Coord, Fort, Terrain, Town, WorldMap

# Proporción objetivo de cada terreno sobre el total de tiles (el resto
# queda como llanura).
TERRAIN_TARGETS = {
    Terrain.FOREST: 0.20,
    Terrain.MOUNTAIN: 0.15,
    Terrain.WATER: 0.10,
}

# Variantes "livianas" y pantano: se tallan de la llanura adyacente a su
# feature (no se listan en TERRAIN_TARGETS porque su cantidad depende del
# perímetro de bosques/montañas/agua ya pintados). La fracción `borde` se
# pinta pegada a la feature; `libre` son manchas sueltas por el mapa (el
# pantano solo aparece junto al agua, así que no tiene parte libre).
EDGE_TARGETS = {
    Terrain.FOREST_LIGHT: {"feature": Terrain.FOREST, "borde": 0.030, "libre": 0.012},
    Terrain.MOUNTAIN_LIGHT: {"feature": Terrain.MOUNTAIN, "borde": 0.028, "libre": 0.008},
    Terrain.MARSH: {"feature": Terrain.WATER, "borde": 0.035, "libre": 0.0},
}

_ORTHO = ((1, 0), (-1, 0), (0, 1), (0, -1))

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
    n_players: int = 2  # jugadores; cada uno arranca con su fuerte inicial

    def __post_init__(self) -> None:
        if self.width < 10 or self.height < 10:
            raise ValueError("el mapa debe ser de al menos 10x10")
        if not 2 <= self.n_players <= MAX_PLAYERS:
            raise ValueError(f"la partida admite de 2 a {MAX_PLAYERS} jugadores")
        if self.n_forts < self.n_players:
            raise ValueError("se necesita al menos un fuerte inicial por jugador")


# Tamaños de mapa preestablecidos: nombre → (ancho, alto, forts, towns). Los usan
# el menú de Nueva partida y el servidor dedicado al crear una partida; viven en
# este módulo puro para que el servidor no dependa de la UI.
MAP_SIZES: dict[str, tuple[int, int, int, int]] = {
    "chico": (22, 15, 3, 4),
    "medio": (30, 20, 4, 6),
    "grande": (44, 29, 6, 9),
    "super grande": (60, 40, 9, 14),
}


def map_params_for(size: str, n_players: int, seed: int) -> MapParams:
    """`MapParams` para un tamaño preestablecido (cae a 'medio' si no existe);
    asegura al menos un fuerte inicial por jugador."""
    width, height, forts, towns = MAP_SIZES.get(size, MAP_SIZES["medio"])
    return MapParams(width, height, max(forts, n_players), towns, seed, n_players=n_players)


def generate_map(params: MapParams, rng: random.Random) -> WorldMap:
    """Genera un mapa aleatorio que cumple las garantías del módulo."""
    for attempt in range(MAX_ATTEMPTS):
        world = _coherent_terrain(params, rng, with_water=attempt < NO_WATER_AFTER)
        if not _place_features(world, params, rng):
            continue
        _drop_orphan_marshes(world)
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
    _paint_edge_variants(world, area, rng)
    return world


def _paint_edge_variants(world: WorldMap, area: int, rng: random.Random) -> None:
    """Talla en la llanura las variantes livianas y el pantano.

    Para cada variante: primero una banda pegada a su feature (bosque ralo en
    el borde de los bosques, colinas alrededor de las montañas, pantano junto
    al agua) y luego, salvo el pantano, algunas manchas sueltas por el mapa.
    Todo es transitable, así que no afecta la conectividad. Si no hay agua
    (mapas del fallback sin agua) no hay llanura costera y el pantano no se
    genera, que es justo lo que se quiere (solo cerca del agua).
    """
    for light, spec in EDGE_TARGETS.items():
        feature = spec["feature"]
        seeds = _plains_adjacent_to(world, feature)
        rng.shuffle(seeds)
        # El pantano no debe alejarse del agua: su crecimiento se confina a la
        # llanura costera (a un paso del agua, incluyendo la banda apenas
        # interior que conecta los tiles de orilla entre sí).
        allowed = set(seeds) if light is Terrain.MARSH else None
        painted = 0
        target_edge = int(area * spec["borde"])
        for seed in seeds:
            if painted >= target_edge:
                break
            painted += _grow_blob(world, seed, rng.randint(1, 4), light, rng, allowed)
        # Manchas sueltas por el mapa (forest-less/mountain-less). El pantano
        # no tiene parte libre: se queda en la banda costera (allowed).
        target_free = int(area * spec["libre"])
        attempts = 0
        while target_free and painted < target_edge + target_free and attempts < 80:
            attempts += 1
            seed = (rng.randrange(world.width), rng.randrange(world.height))
            painted += _grow_blob(world, seed, rng.randint(2, 5), light, rng)


def _drop_orphan_marshes(world: WorldMap) -> None:
    """Devuelve a llanura los pantanos que quedaron sin agua adyacente.

    Al colocar fuertes/pueblos, un tile de agua pegado a un pantano pudo
    volverse llanura, dejando ese pantano lejos del agua. Es raro (~1%), pero
    rompe la regla de que el pantano solo existe junto al agua, así que se
    limpia (a llanura, siempre transitable; no afecta la conectividad)."""
    for y in range(world.height):
        for x in range(world.width):
            if world.tiles[y][x] is not Terrain.MARSH:
                continue
            if not any(
                world.in_bounds((x + dx, y + dy))
                and world.tiles[y + dy][x + dx] is Terrain.WATER
                for dx, dy in _ORTHO
            ):
                world.tiles[y][x] = Terrain.PLAINS


def _plains_adjacent_to(world: WorldMap, feature: Terrain) -> list[Coord]:
    """Tiles de llanura con al menos un vecino ortogonal del terreno `feature`."""
    result: list[Coord] = []
    for y in range(world.height):
        for x in range(world.width):
            if world.tiles[y][x] is not Terrain.PLAINS:
                continue
            if any(
                world.in_bounds((x + dx, y + dy))
                and world.tiles[y + dy][x + dx] is feature
                for dx, dy in _ORTHO
            ):
                result.append((x, y))
    return result


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
    world: WorldMap,
    start: Coord,
    size: int,
    terrain: Terrain,
    rng: random.Random,
    allowed: set[Coord] | None = None,
) -> int:
    """Crece una mancha desde `start` tomando tiles de llanura adyacentes.

    Si se pasa `allowed`, la mancha no se expande fuera de ese conjunto (p. ej.
    el pantano, que no debe alejarse de la costa)."""
    frontier = [start]
    painted = 0
    while frontier and painted < size:
        x, y = frontier.pop(rng.randrange(len(frontier)))
        if allowed is not None and (x, y) not in allowed:
            continue
        if not _paint(world, (x, y), terrain):
            continue
        painted += 1
        for neighbor in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
            if world.in_bounds(neighbor) and rng.random() < 0.8:
                frontier.append(neighbor)
    return painted


def _paint_water(world: WorldMap, target: int, rng: random.Random) -> None:
    """Agua: un río que cruza el mapa (si es lo bastante grande) y lagos.

    Los puentes se construyen al final, con toda el agua ya pintada, para
    poder extenderlos de orilla a orilla (un puente nunca termina en agua).
    """
    painted = 0
    crossings: list[tuple[Coord, bool]] = []  # (posición, río vertical)
    if min(world.width, world.height) >= 15:
        painted += _paint_river(world, rng, crossings)
    for _ in range(100):
        if painted >= target:
            break
        seed = (rng.randrange(world.width), rng.randrange(world.height))
        size = min(rng.randint(3, 9), target - painted)
        painted += _grow_blob(world, seed, size, Terrain.WATER, rng)
    for pos, vertical in crossings:
        _build_bridge(world, pos, vertical)


def _paint_river(
    world: WorldMap, rng: random.Random, crossings: list[tuple[Coord, bool]]
) -> int:
    """Río serpenteante de borde a borde. Cada cierto tramo registra un cruce
    en `crossings` (ahí irá un puente) para que el río no corte el mapa en
    dos, pero con pocos puentes: los justos para mantener la conectividad sin
    anular la barrera de agua (un río acribillado de puentes deja de serlo)."""
    vertical = rng.random() < 0.5
    if vertical:
        x, y = rng.randrange(world.width // 4, 3 * world.width // 4), 0
        main = (0, 1)
        span = world.height
    else:
        x, y = 0, rng.randrange(world.height // 4, 3 * world.height // 4)
        main = (1, 0)
        span = world.width
    painted = 0
    # Espaciado de los puentes proporcional al largo del río: ~2 cruces por
    # río (a veces 1 o 3), no uno cada pocos tiles. El primer cruce siempre
    # cae (span_lo < largo del río), así el río nunca parte el mapa en dos.
    span_lo = max(8, int(span * 0.20))
    span_hi = max(span_lo + 4, int(span * 0.55))
    next_ford = rng.randint(span_lo, span_hi)
    for _ in range(3 * (world.height if vertical else world.width)):
        if not world.in_bounds((x, y)):
            break
        next_ford -= 1
        if next_ford <= 0:
            next_ford = rng.randint(span_lo, span_hi)
            crossings.append(((x, y), vertical))
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


def _build_bridge(world: WorldMap, pos: Coord, vertical_river: bool) -> None:
    """Puente completo: cruza perpendicular al río y se extiende sobre el
    agua en ambos sentidos hasta tocar tierra (nunca termina en agua)."""
    terrain = Terrain.BRIDGE_H if vertical_river else Terrain.BRIDGE_V
    dx, dy = (1, 0) if vertical_river else (0, 1)  # eje del cruce
    world.tiles[pos[1]][pos[0]] = terrain
    for sign in (1, -1):
        x, y = pos
        while True:
            x, y = x + sign * dx, y + sign * dy
            if not world.in_bounds((x, y)) or world.tiles[y][x] is not Terrain.WATER:
                break
            world.tiles[y][x] = terrain


def _place_features(world: WorldMap, params: MapParams, rng: random.Random) -> bool:
    """Coloca fuertes y pueblos; devuelve False si no encontró lugar."""
    taken: list[Coord] = []

    def pick(box: tuple[int, int, int, int]) -> Coord | None:
        x_min, x_max, y_min, y_max = box
        for _ in range(200):
            pos = (rng.randrange(x_min, x_max), rng.randrange(y_min, y_max))
            if all(_manhattan(pos, t) >= MIN_FEATURE_DISTANCE for t in taken):
                return pos
        return None

    whole = (0, world.width, 0, world.height)
    # Fuertes iniciales de los jugadores, repartidos en zonas alejadas.
    player_positions = [pick(zone) for zone in _player_zones(world, params.n_players)]
    if None in player_positions:
        return False
    for player_id, pos in enumerate(player_positions):
        taken.append(pos)
        world.forts.append(Fort(position=pos, owner=player_id))

    # Resto de fuertes (neutrales) y pueblos, en cualquier parte.
    for _ in range(params.n_forts - params.n_players):
        pos = pick(whole)
        if pos is None:
            return False
        taken.append(pos)
        world.forts.append(Fort(position=pos))
    for _ in range(params.n_towns):
        pos = pick(whole)
        if pos is None:
            return False
        taken.append(pos)
        world.towns.append(Town(position=pos))

    # Los tiles con fuerte/pueblo siempre son transitables.
    for x, y in taken:
        world.tiles[y][x] = Terrain.PLAINS
    return True


def _player_zones(world: WorldMap, n_players: int) -> list[tuple[int, int, int, int]]:
    """Cajas (x_min, x_max, y_min, y_max) donde nace el fuerte inicial de cada
    jugador, repartidas para que arranquen lo más separados posible.

    Con 2 jugadores son las bandas izquierda/derecha de siempre (preserva el
    layout clásico y el determinismo de seeds existentes); con 3-4, esquinas.
    """
    bx = max(2, world.width // 5)
    by = max(2, world.height // 5)
    w, h = world.width, world.height
    if n_players <= 2:
        return [(0, bx, 0, h), (w - bx, w, 0, h)][:n_players]
    corners = [
        (0, bx, 0, by),          # superior izquierda
        (w - bx, w, 0, by),      # superior derecha
        (0, bx, h - by, h),      # inferior izquierda
        (w - bx, w, h - by, h),  # inferior derecha
    ]
    return corners[:n_players]


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
