"""Mapa del mundo: grilla de tiles, terrenos, fuertes y pueblos."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

Coord = tuple[int, int]  # (x, y)

NEUTRAL = -1  # owner de forts/towns sin dueño
MAX_PLAYERS = 4  # tope de jugadores por partida (free-for-all, v1: humano + IAs)


class Terrain(str, Enum):
    PLAINS = "plains"
    FOREST = "forest"
    MOUNTAIN = "mountain"
    WATER = "water"  # intransitable
    BRIDGE_H = "bridge_h"  # puente sobre el agua, transitable (tablones E-O)
    BRIDGE_V = "bridge_v"  # ídem, tablones N-S


@dataclass
class Fort:
    position: Coord
    owner: int = NEUTRAL
    has_flag: bool = True
    # Tropas producidas que esperan en el fuerte. Solo salen al mapa cuando
    # el jugador crea un ejército (CreateArmyOrder) o reabastece uno propio
    # estacionado en el fuerte. Se pierde al ser capturado.
    reserve: dict[str, int] = field(default_factory=dict)

    @property
    def reserve_total(self) -> int:
        return sum(self.reserve.values())


@dataclass
class Town:
    position: Coord
    owner: int = NEUTRAL
    has_flag: bool = False


@dataclass
class WorldMap:
    width: int
    height: int
    tiles: list[list[Terrain]]  # tiles[y][x]
    forts: list[Fort] = field(default_factory=list)
    towns: list[Town] = field(default_factory=list)

    def in_bounds(self, pos: Coord) -> bool:
        x, y = pos
        return 0 <= x < self.width and 0 <= y < self.height

    def terrain_at(self, pos: Coord) -> Terrain:
        x, y = pos
        return self.tiles[y][x]

    def is_passable(self, pos: Coord) -> bool:
        return self.in_bounds(pos) and self.terrain_at(pos) != Terrain.WATER

    def fort_at(self, pos: Coord) -> Fort | None:
        return next((f for f in self.forts if f.position == pos), None)

    def town_at(self, pos: Coord) -> Town | None:
        return next((t for t in self.towns if t.position == pos), None)

    def can_step(self, origin: Coord, dest: Coord) -> bool:
        """¿Es válido un paso de `origin` a `dest` (tiles adyacentes)?

        Los puentes son túneles de un solo eje: BRIDGE_H conecta solamente
        este-oeste y BRIDGE_V solamente norte-sur, tanto para entrar como
        para salir — no se puede salir por el lateral aunque haya tierra u
        otro puente en el casillero contiguo.
        """
        if not (self.is_passable(origin) and self.is_passable(dest)):
            return False
        dx = dest[0] - origin[0]
        dy = dest[1] - origin[1]
        if abs(dx) + abs(dy) != 1:
            return False
        for tile in (self.terrain_at(origin), self.terrain_at(dest)):
            if tile is Terrain.BRIDGE_H and dx == 0:
                return False
            if tile is Terrain.BRIDGE_V and dy == 0:
                return False
        return True

    def neighbors(self, pos: Coord) -> list[Coord]:
        """Vecinos alcanzables en un paso (transitables y, en puentes,
        respetando el eje del túnel)."""
        x, y = pos
        candidates = [(x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)]
        return [c for c in candidates if self.can_step(pos, c)]

    def to_dict(self) -> dict:
        """Serialización a dict (para savegames y futuro editor de mapas).

        Los tiles se codifican como una string por fila ('p', 'f', 'm', 'w'):
        compacto y editable a mano en JSON.
        """
        return {
            "width": self.width,
            "height": self.height,
            "tiles": ["".join(TERRAIN_TO_CHAR[t] for t in row) for row in self.tiles],
            "forts": [
                {
                    "position": list(f.position),
                    "owner": f.owner,
                    "has_flag": f.has_flag,
                    "reserve": dict(f.reserve),
                }
                for f in self.forts
            ],
            "towns": [
                {"position": list(t.position), "owner": t.owner, "has_flag": t.has_flag}
                for t in self.towns
            ],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "WorldMap":
        return cls(
            width=data["width"],
            height=data["height"],
            tiles=[[CHAR_TO_TERRAIN[c] for c in row] for row in data["tiles"]],
            forts=[
                Fort(
                    position=tuple(f["position"]),
                    owner=f["owner"],
                    has_flag=f["has_flag"],
                    reserve=dict(f.get("reserve", {})),
                )
                for f in data["forts"]
            ],
            towns=[
                Town(position=tuple(t["position"]), owner=t["owner"], has_flag=t["has_flag"])
                for t in data["towns"]
            ],
        )


TERRAIN_TO_CHAR = {
    Terrain.PLAINS: "p",
    Terrain.FOREST: "f",
    Terrain.MOUNTAIN: "m",
    Terrain.WATER: "w",
    Terrain.BRIDGE_H: "h",
    Terrain.BRIDGE_V: "v",
}
CHAR_TO_TERRAIN = {c: t for t, c in TERRAIN_TO_CHAR.items()}
