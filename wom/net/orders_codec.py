"""Serialización de `Order` ↔ dict para enviarlas por la red.

Las dataclasses de `core/orders.py` son inmutables y no saben serializarse
(el core no conoce la red). El codec vive acá para no contaminar el core. Cada
orden se codifica como `{"kind": "<tipo>", ...}` y se reconstruye exacta al
decodificar (tuplas incluidas, para que las órdenes sigan siendo hasheables).
"""

from __future__ import annotations

from wom.core.orders import (
    CreateArmyOrder,
    MergeArmyOrder,
    MoveOrder,
    Order,
    SplitArmyOrder,
    TransferTroopsOrder,
)
from wom.core.worldmap import Coord


def encode_order(order: Order) -> dict:
    """Convierte una orden en un dict JSON-serializable."""
    if isinstance(order, MoveOrder):
        return {
            "kind": "move",
            "army_id": order.army_id,
            "path": [list(tile) for tile in order.path],
        }
    if isinstance(order, CreateArmyOrder):
        return {"kind": "create", "position": list(order.position)}
    if isinstance(order, MergeArmyOrder):
        return {
            "kind": "merge",
            "source_id": order.source_id,
            "target_id": order.target_id,
        }
    if isinstance(order, SplitArmyOrder):
        return {
            "kind": "split",
            "source_id": order.source_id,
            "composition": [[cls, n] for cls, n in order.composition],
        }
    if isinstance(order, TransferTroopsOrder):
        return {
            "kind": "transfer",
            "source_id": order.source_id,
            "target_id": order.target_id,
            "composition": [[cls, n] for cls, n in order.composition],
        }
    raise TypeError(f"Orden no serializable: {order!r}")


def decode_order(data: dict) -> Order:
    """Reconstruye una orden a partir de su dict (inverso de `encode_order`)."""
    kind = data["kind"]
    if kind == "move":
        return MoveOrder(
            army_id=data["army_id"],
            path=tuple(_coord(tile) for tile in data["path"]),
        )
    if kind == "create":
        return CreateArmyOrder(position=_coord(data["position"]))
    if kind == "merge":
        return MergeArmyOrder(
            source_id=data["source_id"], target_id=data["target_id"]
        )
    if kind == "split":
        return SplitArmyOrder(
            source_id=data["source_id"],
            composition=tuple((cls, n) for cls, n in data["composition"]),
        )
    if kind == "transfer":
        return TransferTroopsOrder(
            source_id=data["source_id"],
            target_id=data["target_id"],
            composition=tuple((cls, n) for cls, n in data["composition"]),
        )
    raise ValueError(f"Tipo de orden desconocido: {kind!r}")


def encode_orders(orders: list[Order]) -> list[dict]:
    return [encode_order(o) for o in orders]


def decode_orders(data: list[dict]) -> list[Order]:
    return [decode_order(d) for d in data]


def _coord(pair) -> Coord:
    """Lista/tupla [x, y] → Coord (tupla de int)."""
    x, y = pair
    return (int(x), int(y))
