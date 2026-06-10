"""Órdenes que los jugadores (humano o AI) emiten en la fase de órdenes.

La UI y la AI producen exactamente el mismo tipo de órdenes: esto hace a los
jugadores intercambiables y permite simular partidas AI vs AI headless.
"""

from __future__ import annotations

from dataclasses import dataclass

from wom.core.worldmap import Coord


@dataclass(frozen=True)
class MoveOrder:
    """Asigna a un ejército el camino (lista de tiles) que debe seguir."""

    army_id: int
    path: tuple[Coord, ...]


@dataclass(frozen=True)
class CreateArmyOrder:
    """Crea un ejército en un fuerte propio con tropas de su reserva.

    Acción voluntaria del jugador: las tropas producidas se acumulan en la
    reserva del fuerte y no salen al mapa hasta que se emite esta orden.
    El ejército nuevo toma hasta `max_army_size` tropas de la reserva.
    """

    position: Coord  # tile del fuerte


Order = MoveOrder | CreateArmyOrder
