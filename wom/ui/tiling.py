"""Autotiling del agua: elige la variante de tile según la costa.

Módulo puro (sin pygame) para poder testearlo headless. Para cada tile de
agua se mira qué vecinos ortogonales son tierra y se elige el asset que
corresponde. Las 16 combinaciones posibles están cubiertas:

- `water`              sin costa (rodeado de agua)
- `water_n/s/e/w`      costa en un lateral
- `water_ne/nw/se/sw`  costa en dos lados adyacentes (esquina)
- `water_ns`           canal horizontal (costa norte y sur)
- `water_ew`           canal vertical (costa este y oeste)
- `water_u_n/s/e/w`    en U: costa en tres lados, con salida hacia ese punto
- `water_single`       charco rodeado de tierra

Los puentes y el borde del mapa cuentan como agua: el río sigue por abajo
del puente y "continúa" fuera del mapa.
"""

from __future__ import annotations

from wom.core.worldmap import Coord, Terrain, WorldMap

# Variante → lados con costa (n/s/e/o). Cubre las 16 combinaciones.
_VARIANT_SIDES: dict[str, frozenset[str]] = {
    "water": frozenset(),
    "water_n": frozenset({"n"}),
    "water_s": frozenset({"s"}),
    "water_e": frozenset({"e"}),
    "water_w": frozenset({"w"}),
    "water_ne": frozenset({"n", "e"}),
    "water_nw": frozenset({"n", "w"}),
    "water_se": frozenset({"s", "e"}),
    "water_sw": frozenset({"s", "w"}),
    "water_ns": frozenset({"n", "s"}),
    "water_ew": frozenset({"e", "w"}),
    "water_u_n": frozenset({"s", "e", "w"}),  # salida al norte
    "water_u_s": frozenset({"n", "e", "w"}),  # salida al sur
    "water_u_e": frozenset({"n", "s", "w"}),  # salida al este
    "water_u_w": frozenset({"n", "s", "e"}),  # salida al oeste
    "water_single": frozenset({"n", "s", "e", "w"}),
}
WATER_VARIANTS = tuple(_VARIANT_SIDES)
_BY_COAST = {sides: name for name, sides in _VARIANT_SIDES.items()}

_DIRECTIONS = {"n": (0, -1), "s": (0, 1), "e": (1, 0), "w": (-1, 0)}
_WATERLIKE = {Terrain.WATER, Terrain.BRIDGE_H, Terrain.BRIDGE_V}
WATERLIKE = frozenset(_WATERLIKE)  # público: los tiles que se dibujan como agua

# Esquinas de costa (overlay). Para cada esquina, las dos direcciones
# ortogonales que la forman y su diagonal (Δx, Δy). Se superpone el overlay
# cuando ambos vecinos ortogonales son agua pero la diagonal es tierra: una
# punta de tierra que asoma en diagonal. El autotiling ortogonal de 16
# variantes no cubre ese caso y sin el parche el corte entre tiles queda
# abrupto. El overlay es un PNG con transparencia que se pinta sobre el tile
# de agua, así no hace falta ningún tile nuevo en el modelo/editor/generador.
_CORNERS: dict[str, tuple[Coord, Coord, Coord]] = {
    "water_corner_ne": ((0, -1), (1, 0), (1, -1)),
    "water_corner_nw": ((0, -1), (-1, 0), (-1, -1)),
    "water_corner_se": ((0, 1), (1, 0), (1, 1)),
    "water_corner_sw": ((0, 1), (-1, 0), (-1, 1)),
}
WATER_CORNER_VARIANTS = tuple(_CORNERS)


def _is_land(world: WorldMap, pos: Coord) -> bool:
    """Tierra = dentro del mapa y no es agua/puente. El borde del mapa cuenta
    como agua (el río continúa fuera), igual que en `water_tile`."""
    return world.in_bounds(pos) and world.terrain_at(pos) not in _WATERLIKE


def water_tile(world: WorldMap, pos: Coord) -> str:
    """Nombre del asset de agua para el tile `pos` (que debe ser agua)."""
    x, y = pos
    land = frozenset(
        side
        for side, (dx, dy) in _DIRECTIONS.items()
        if _is_land(world, (x + dx, y + dy))
    )
    return _BY_COAST[land]


def water_corners(world: WorldMap, pos: Coord) -> frozenset[str]:
    """Overlays de esquina (water_corner_*) a superponer sobre el tile `pos`.

    Una esquina se dibuja cuando los dos vecinos ortogonales que la forman son
    agua y la diagonal es tierra (una punta de tierra asomando en diagonal).
    Puede combinarse con cualquier variante de `water_tile` (p. ej. una orilla
    recta al norte más una punta de tierra en el sudeste)."""
    x, y = pos
    return frozenset(
        name
        for name, (o1, o2, d) in _CORNERS.items()
        if not _is_land(world, (x + o1[0], y + o1[1]))
        and not _is_land(world, (x + o2[0], y + o2[1]))
        and _is_land(world, (x + d[0], y + d[1]))
    )


# --- autotiling de terreno seco (bordes fluidos entre terrenos) ------------
# Prioridad de "dominancia" visual: el terreno más alto derrama su textura,
# suavizada, sobre el vecino más bajo (como la costa del agua hace con la
# tierra). El agua/puentes quedan fuera: ya los resuelve el autotiling de costa.
TERRAIN_PRIORITY: dict[Terrain, int] = {
    Terrain.PLAINS: 0,
    Terrain.MARSH: 1,
    Terrain.FOREST_LIGHT: 2,
    Terrain.MOUNTAIN_LIGHT: 3,
    Terrain.FOREST: 4,
    Terrain.MOUNTAIN: 5,
}

# Esquinas para el derrame diagonal: dos ortogonales + la diagonal (Δx, Δy).
_DRY_CORNERS: dict[str, tuple[Coord, Coord, Coord]] = {
    "ne": ((0, -1), (1, 0), (1, -1)),
    "nw": ((0, -1), (-1, 0), (-1, -1)),
    "se": ((0, 1), (1, 0), (1, 1)),
    "sw": ((0, 1), (-1, 0), (-1, 1)),
}


def _dry_priority(world: WorldMap, pos: Coord) -> int | None:
    """Prioridad del terreno seco en `pos`, o None si está fuera del mapa o es
    agua/puente (que no participa del derrame de terreno seco)."""
    if not world.in_bounds(pos):
        return None
    terrain = world.terrain_at(pos)
    if terrain in _WATERLIKE:
        return None
    return TERRAIN_PRIORITY.get(terrain, 0)


def dry_edges(world: WorldMap, pos: Coord) -> list[tuple[str, Terrain]]:
    """Bordes a superponer sobre el tile seco `pos`: por cada vecino ortogonal
    de MAYOR prioridad, `(lado, terreno_del_vecino)`.

    El renderer pinta la textura del vecino enmascarada por ese lado, así el
    terreno dominante se funde con el más bajo en vez de cortar en cuadrado.
    Devuelve [] para agua/puentes (los maneja el autotiling de costa)."""
    base = _dry_priority(world, pos)
    if base is None:
        return []
    x, y = pos
    edges: list[tuple[str, Terrain]] = []
    for side, (dx, dy) in _DIRECTIONS.items():
        npos = (x + dx, y + dy)
        npri = _dry_priority(world, npos)
        if npri is not None and npri > base:
            edges.append((side, world.terrain_at(npos)))
    return edges


def dry_corners(world: WorldMap, pos: Coord) -> list[tuple[str, Terrain]]:
    """Derrame diagonal sobre `pos`: por cada esquina cuyo vecino diagonal es de
    mayor prioridad y cuyos dos ortogonales NO lo son (una punta que asoma en
    diagonal, no cubierta por un borde recto), `(esquina, terreno_diagonal)`."""
    base = _dry_priority(world, pos)
    if base is None:
        return []
    x, y = pos
    corners: list[tuple[str, Terrain]] = []
    for name, (o1, o2, d) in _DRY_CORNERS.items():
        dpos = (x + d[0], y + d[1])
        dpri = _dry_priority(world, dpos)
        if dpri is None or dpri <= base:
            continue
        p1 = _dry_priority(world, (x + o1[0], y + o1[1]))
        p2 = _dry_priority(world, (x + o2[0], y + o2[1]))
        if (p1 is None or p1 <= base) and (p2 is None or p2 <= base):
            corners.append((name, world.terrain_at(dpos)))
    return corners


# --- grupos para el contorno de tinta --------------------------------------
# Clases cartográficas: una línea de tinta marca el límite entre dos grupos
# distintos (costa, linde de bosque, pie de montaña, borde de pantano). Los
# tiles dentro del mismo grupo no llevan contorno.
_INK_GROUP: dict[Terrain, str] = {
    Terrain.PLAINS: "plains",
    Terrain.FOREST: "forest",
    Terrain.FOREST_LIGHT: "forest",
    Terrain.MOUNTAIN: "mountain",
    Terrain.MOUNTAIN_LIGHT: "mountain",
    Terrain.MARSH: "marsh",
    Terrain.WATER: "water",
    Terrain.BRIDGE_H: "water",
    Terrain.BRIDGE_V: "water",
}


def ink_group(terrain: Terrain) -> str:
    """Clase cartográfica de un terreno (para decidir dónde va el contorno)."""
    return _INK_GROUP.get(terrain, "plains")
