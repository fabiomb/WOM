"""Animación del movimiento al finalizar el turno.

El core registra en `Game.last_moves` los tiles que pisó cada ejército;
esta clase interpola esas posiciones en el tiempo para que la UI dibuje el
turno de forma fluida en vez de teletransportar los ejércitos. Todos los
ejércitos animan en simultáneo, a velocidad constante en tiles por segundo.

Módulo sin pygame a propósito: la interpolación es pura matemática y se
testea sin display (el tiempo entra como parámetro).
"""

from __future__ import annotations

from dataclasses import dataclass

from wom.core.worldmap import Coord

TILES_PER_SECOND = 6.0

# Posición fraccionaria en coordenadas de tile (para interpolar entre tiles).
FloatCoord = tuple[float, float]


@dataclass(frozen=True)
class ArmyMotion:
    """Lo necesario para dibujar un ejército en marcha, congelado al animar.

    `alive` distingue a los que mueren en el turno: animan su recorrido
    completo y desaparecen cuando la animación termina (queda su cruz).
    """

    army_id: int
    owner: int
    class_id: str  # clase dominante (elige el sprite)
    troops: int
    waypoints: list[Coord]  # tiles pisados este turno; [pos] si no se movió
    alive: bool = True

    def position_at(self, elapsed_seconds: float) -> FloatCoord:
        """Posición interpolada tras `elapsed_seconds` de animación."""
        progress = elapsed_seconds * TILES_PER_SECOND  # en tiles recorridos
        last = len(self.waypoints) - 1
        if progress >= last:
            return tuple(map(float, self.waypoints[-1]))
        i = int(progress)
        t = progress - i
        (x0, y0), (x1, y1) = self.waypoints[i], self.waypoints[i + 1]
        return (x0 + (x1 - x0) * t, y0 + (y1 - y0) * t)

    @property
    def duration(self) -> float:
        """Segundos que tarda en recorrer sus waypoints."""
        return (len(self.waypoints) - 1) / TILES_PER_SECOND


class TurnAnimation:
    """Anima todos los movimientos de un turno; `finished` cuando todos llegan."""

    def __init__(self, motions: list[ArmyMotion]):
        self.motions = motions
        self.duration = max((m.duration for m in motions), default=0.0)
        self._skipped = False

    def finished(self, elapsed_seconds: float) -> bool:
        return self._skipped or elapsed_seconds >= self.duration

    def skip(self) -> None:
        """Corta la animación (Enter/Espacio/click): el estado final ya existe."""
        self._skipped = True

    def positions(self, elapsed_seconds: float) -> list[tuple[ArmyMotion, FloatCoord]]:
        return [(m, m.position_at(elapsed_seconds)) for m in self.motions]


def build_turn_animation(game, pre_turn_armies: list[dict]) -> TurnAnimation | None:
    """Arma la animación tras `run_turn`, o None si nadie se movió.

    `pre_turn_armies` es el snapshot tomado antes del turno (dicts de
    Army.to_dict()): conserva sprite y posición de los ejércitos que
    murieron durante el turno. Los creados este turno (no están en el
    snapshot) aparecen quietos en su fuerte.
    """
    motions: list[ArmyMotion] = []
    seen: set[int] = set()
    for data in pre_turn_armies:
        army_id = data["id"]
        seen.add(army_id)
        current = game.army_by_id(army_id)
        waypoints = game.last_moves.get(army_id) or [tuple(data["position"])]
        # El conteo final evita que el número "salte" al terminar la animación.
        troops = current.total_troops if current else sum(data["composition"].values())
        composition = current.composition if current else data["composition"]
        motions.append(
            ArmyMotion(
                army_id=army_id,
                owner=data["owner"],
                class_id=_dominant(composition),
                troops=troops,
                waypoints=[tuple(p) for p in waypoints],
                alive=current is not None,
            )
        )
    for army in game.armies:  # creados durante el turno: estáticos
        if army.id not in seen:
            motions.append(
                ArmyMotion(
                    army_id=army.id,
                    owner=army.owner,
                    class_id=_dominant(army.composition),
                    troops=army.total_troops,
                    waypoints=[army.position],
                )
            )
    if all(len(m.waypoints) <= 1 for m in motions):
        return None  # turno sin movimiento: nada que animar
    return TurnAnimation(motions)


def _dominant(composition: dict[str, int]) -> str:
    return max(composition, key=lambda c: composition[c])
