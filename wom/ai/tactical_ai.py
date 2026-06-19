"""IA del combate táctico en tiempo real (wom/core/tactical.py).

Conduce las fichas de un bando durante la batalla con zoom **según el nivel del
ejército rival** (facil/medio/dificil), igual que el resto del juego: los
parámetros salen de `ai.json` (bloque `tactico` por nivel), no hardcodeados.

Todas las fichas **empujan** hacia el enemigo más cercano (así la batalla
siempre avanza, sin estancarse). La habilidad del nivel está en la **selección
de blanco**: prioriza a los enemigos de mayor amenaza —alto ataque y poca
defensa, es decir arqueros y caballería— dentro de un radio `prio_rango`,
porque eliminarlos rinde más que castigar a la infantería resistente:

- **facil**: `prio_rango` 0 → sin priorizar, pega al más cercano, reasigna lento.
- **medio**: prioriza amenazas en un radio chico, cadencia media.
- **dificil**: prioriza en un radio amplio y reasigna rápido — el más fuerte.

Sin pygame: la conduce la UI llamando `update(battle, dt)` una vez por frame.
"""

from __future__ import annotations

import math

from wom.core.config import load_ai_config
from wom.core.tactical import TacticalBattle, Unit

# Parámetros por defecto (si falta el bloque o el nivel en ai.json).
_DEFAULT = {"prio_rango": 6.0, "intervalo": 0.5}


class TacticalAI:
    """Da órdenes a las fichas de `owner` con la habilidad de su nivel."""

    def __init__(self, owner: int, *, interval: float = 0.5, prio_range: float = 6.0):
        self.owner = owner
        self.interval = interval
        self.prio_range = prio_range
        self._timer = 0.0

    @classmethod
    def for_level(cls, owner: int, level: str | None) -> "TacticalAI":
        """Construye la IA táctica con los parámetros del nivel (ai.json)."""
        cfg = (load_ai_config().get(level) or {}).get("tactico", _DEFAULT)
        return cls(
            owner,
            interval=cfg.get("intervalo", _DEFAULT["intervalo"]),
            prio_range=cfg.get("prio_rango", _DEFAULT["prio_rango"]),
        )

    def update(self, battle: TacticalBattle, dt: float) -> None:
        self._timer -= dt
        if self._timer > 0.0:
            return
        self._timer = self.interval
        mine = [u for u in battle.units if u.owner == self.owner and u.active]
        enemies = [u for u in battle.units if u.owner != self.owner and u.active]
        if not mine or not enemies:
            return
        classes = battle.classes
        for u in mine:
            target = self._pick_target(u, enemies, classes)
            if target is not None:
                battle.command([u.id], "attack", target.id)

    def _pick_target(self, u: Unit, enemies: list[Unit], classes) -> Unit | None:
        """El más cercano por defecto; si hay enemigos de alta amenaza dentro de
        `prio_range`, prioriza al de mayor ataque/defensa (el más letal y frágil)."""
        def dist(e: Unit) -> float:
            return math.hypot(e.x - u.x, e.y - u.y)

        nearest = min(enemies, key=dist)
        if self.prio_range > 0:
            in_range = [e for e in enemies if dist(e) <= self.prio_range]
            if in_range:
                return max(
                    in_range,
                    key=lambda e: classes[e.class_id].ataque
                    / max(1, classes[e.class_id].defensa),
                )
        return nearest
