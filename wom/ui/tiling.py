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


def water_tile(world: WorldMap, pos: Coord) -> str:
    """Nombre del asset de agua para el tile `pos` (que debe ser agua)."""
    x, y = pos
    land = frozenset(
        side
        for side, (dx, dy) in _DIRECTIONS.items()
        if world.in_bounds((x + dx, y + dy))
        and world.terrain_at((x + dx, y + dy)) not in _WATERLIKE
    )
    return _BY_COAST[land]
