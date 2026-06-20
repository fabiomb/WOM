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
