"""Condiciones de victoria, evaluadas al final de cada turno.

Orden de evaluación:
1. Victoria total (se chequea siempre, en cualquier modo): el rival no
   tiene ejércitos ni fuertes.
2. Banderas (solo modo FLAGS): un jugador controla todas las banderas.
3. Tiempo (solo modo TIME): al llegar al turno límite gana quien controla
   más territorio (forts + towns); en empate, quien tiene más tropas;
   si persiste el empate, la partida termina sin ganador.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from wom.core.game import Game


class VictoryMode(str, Enum):
    """Modo elegido al crear la partida."""

    TOTAL = "total"          # eliminar todos los ejércitos y fuertes del rival
    FLAGS = "flags"          # capturar todas las banderas (forts/towns marcados)
    TIME = "time"            # al turno límite, gana quien tenga superioridad


@dataclass(frozen=True)
class VictoryResult:
    is_over: bool
    winner: int | None  # player_id; None con is_over=True significa empate
    mode: VictoryMode | None = None
    reason: str = ""


ONGOING = VictoryResult(is_over=False, winner=None)


def check_victory(game: "Game", mode: VictoryMode) -> VictoryResult:
    result = _check_total(game)
    if result.is_over:
        return result
    if mode is VictoryMode.FLAGS:
        result = _check_flags(game)
        if result.is_over:
            return result
    # Tope de turnos: un `turn_limit` explícito (lo fija el host en red) actúa
    # como límite duro en cualquier modo; sin él, solo el modo TIME usa el
    # default de config. Al alcanzarlo se desempata por territorio/tropas.
    limit = game.turn_limit
    if limit is None and mode is VictoryMode.TIME:
        limit = game.config["turnos_limite_default"]
    if limit is not None and game.turn >= limit:
        return _check_time(game)
    return ONGOING


def _check_total(game: "Game") -> VictoryResult:
    """Un jugador sin ejércitos ni fuertes está eliminado."""
    alive = [
        p.id
        for p in game.players
        if game.armies_of(p.id) or any(f.owner == p.id for f in game.world.forts)
    ]
    if len(alive) == 1:
        return VictoryResult(
            is_over=True,
            winner=alive[0],
            mode=VictoryMode.TOTAL,
            reason="el rival no tiene ejércitos ni fuertes",
        )
    if not alive:  # aniquilación mutua en el mismo turno
        return VictoryResult(is_over=True, winner=None, mode=VictoryMode.TOTAL, reason="empate")
    return ONGOING


def _check_flags(game: "Game") -> VictoryResult:
    flagged = [f for f in game.world.forts if f.has_flag] + [
        t for t in game.world.towns if t.has_flag
    ]
    if not flagged:
        return ONGOING
    owners = {f.owner for f in flagged}
    if len(owners) == 1 and (winner := owners.pop()) >= 0:
        return VictoryResult(
            is_over=True,
            winner=winner,
            mode=VictoryMode.FLAGS,
            reason="controla todas las banderas",
        )
    return ONGOING


def _check_time(game: "Game") -> VictoryResult:
    def territory(player_id: int) -> int:
        return sum(1 for f in game.world.forts if f.owner == player_id) + sum(
            1 for t in game.world.towns if t.owner == player_id
        )

    def troops(player_id: int) -> int:
        return sum(a.total_troops for a in game.armies_of(player_id))

    scores = {p.id: (territory(p.id), troops(p.id)) for p in game.players}
    best = max(scores.values())
    winners = [pid for pid, score in scores.items() if score == best]
    if len(winners) == 1:
        return VictoryResult(
            is_over=True,
            winner=winners[0],
            mode=VictoryMode.TIME,
            reason="superioridad de territorio y tropas al turno límite",
        )
    return VictoryResult(
        is_over=True, winner=None, mode=VictoryMode.TIME, reason="empate al turno límite"
    )
