"""Reglas de una partida en red que define el host.

Viajan en `GameSetup.rules`. El modo de victoria, el tamaño de mapa y la seed
no van acá: quedan horneados en el estado inicial del juego (`Game.to_dict()`),
que el host construye y el cliente reconstruye tal cual. Acá van solo las
reglas que la **sesión/UI** hace cumplir, no el core:

- `turn_seconds`: tiempo por turno (0 = infinito).
- `max_turns`: turnos máximos antes de evaluar el desempate.

La aplicación efectiva del reloj de turno y del límite es de la fase MP5; esta
dataclass ya transporta los valores configurados por el host.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MatchRules:
    turn_seconds: int = 0
    max_turns: int = 50

    def to_dict(self) -> dict:
        return {"turn_seconds": self.turn_seconds, "max_turns": self.max_turns}

    @classmethod
    def from_dict(cls, data: dict) -> "MatchRules":
        return cls(
            turn_seconds=int(data.get("turn_seconds", 0)),
            max_turns=int(data.get("max_turns", 50)),
        )
