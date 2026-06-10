"""Ejércitos: composición por clases, XP, alimentación y órdenes de movimiento."""

from __future__ import annotations

from dataclasses import dataclass, field

from wom.core.config import UnitClass
from wom.core.worldmap import Coord


@dataclass
class Army:
    id: int
    owner: int  # player_id
    position: Coord
    composition: dict[str, int]  # {clase_id: cantidad}, suma <= max_army_size
    xp: int = 100
    food: int = 100  # 0..100, escala la eficiencia en combate
    path: list[Coord] = field(default_factory=list)  # camino pendiente (órdenes)

    @property
    def total_troops(self) -> int:
        return sum(self.composition.values())

    @property
    def is_destroyed(self) -> bool:
        """XP en cero o sin tropas elimina el ejército (cruz en el mapa)."""
        return self.xp <= 0 or self.total_troops <= 0

    def speed(self, classes: dict[str, UnitClass]) -> int:
        """La velocidad del ejército es la de su clase más lenta presente."""
        present = [classes[c].velocidad for c, n in self.composition.items() if n > 0]
        return min(present) if present else 0

    def apply_losses(self, losses: dict[str, int]) -> None:
        """Descuenta bajas por clase tras una batalla, sin ir debajo de cero."""
        for class_id, n in losses.items():
            current = self.composition.get(class_id, 0)
            self.composition[class_id] = max(0, current - n)

    def to_dict(self) -> dict:
        """Serialización a dict (savegames)."""
        return {
            "id": self.id,
            "owner": self.owner,
            "position": list(self.position),
            "composition": dict(self.composition),
            "xp": self.xp,
            "food": self.food,
            "path": [list(step) for step in self.path],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Army":
        return cls(
            id=data["id"],
            owner=data["owner"],
            position=tuple(data["position"]),
            composition=dict(data["composition"]),
            xp=data["xp"],
            food=data["food"],
            path=[tuple(step) for step in data["path"]],
        )
