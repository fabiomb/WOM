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


@dataclass(frozen=True)
class MergeArmyOrder:
    """Fusiona `source` entero dentro de `target` (deben ser aledaños y del
    mismo dueño). Es la versión "orden" de `Game.merge_armies`: la usa la AI
    para consolidar ejércitos dispersos (el humano fusiona con Shift+click,
    de forma inmediata).
    """

    source_id: int
    target_id: int


@dataclass(frozen=True)
class SplitArmyOrder:
    """Divide `source`: las tropas de `composition` forman un ejército nuevo
    en un tile aledaño libre (versión "orden" de `Game.split_army`). La usa
    la AI para dejar una guarnición en un fuerte y proyectar el resto (el
    humano divide con el botón/tecla D, de forma inmediata).

    `composition` es una tupla de pares (clase, cantidad) para que la orden
    sea inmutable y hasheable como las demás.
    """

    source_id: int
    composition: tuple[tuple[str, int], ...]


Order = MoveOrder | CreateArmyOrder | MergeArmyOrder | SplitArmyOrder
