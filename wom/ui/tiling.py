"""Autotiling del agua: elige la variante de tile según la costa.

Módulo puro (sin pygame) para poder testearlo headless. Para cada tile de
agua se mira qué vecinos ortogonales son tierra y se elige uno de los diez
assets: `water` (rodeado de agua), `water_n/s/e/w` (costa en un lateral),
`water_ne/nw/se/sw` (costa en dos lados adyacentes, esquina) o
`water_single` (charco rodeado de tierra).

Combinaciones sin asset exacto (canales de un tile de ancho, tres costas)
caen a la variante disponible más parecida — placeholder aceptable hasta
que exista arte específico. Los puentes y el borde del mapa cuentan como
agua: el río sigue por abajo del puente y "continúa" fuera del mapa.
"""

from __future__ import annotations

from wom.core.worldmap import Coord, Terrain, WorldMap

WATER_VARIANTS = (
    "water", "water_n", "water_s", "water_e", "water_w",
    "water_ne", "water_nw", "water_se", "water_sw", "water_single",
)

# Lados con costa que representa cada asset (n/s/e/o en el tile).
_VARIANT_SIDES: dict[str, frozenset[str]] = {
    "water_ne": frozenset({"n", "e"}),
    "water_nw": frozenset({"n", "w"}),
    "water_se": frozenset({"s", "e"}),
    "water_sw": frozenset({"s", "w"}),
    "water_n": frozenset({"n"}),
    "water_s": frozenset({"s"}),
    "water_e": frozenset({"e"}),
    "water_w": frozenset({"w"}),
}
_EXACT = {sides: name for name, sides in _VARIANT_SIDES.items()}

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
    if not land:
        return "water"
    if land in _EXACT:
        return _EXACT[land]
    if len(land) == 4:
        return "water_single"
    # sin asset exacto: la variante cuyos lados estén todos en la costa real,
    # priorizando esquinas (el orden de _VARIANT_SIDES es determinista)
    for name, sides in _VARIANT_SIDES.items():
        if sides <= land:
            return name
    return "water"  # pragma: no cover - inalcanzable (len(land) >= 1)
