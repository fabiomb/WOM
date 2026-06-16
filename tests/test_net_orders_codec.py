"""Round-trip de la serialización de órdenes (codec de red)."""

from wom.core.orders import (
    CreateArmyOrder,
    MergeArmyOrder,
    MoveOrder,
    SplitArmyOrder,
    TransferTroopsOrder,
)
from wom.net.orders_codec import (
    decode_order,
    decode_orders,
    encode_order,
    encode_orders,
)

ORDERS = [
    MoveOrder(army_id=3, path=((1, 2), (1, 3), (2, 3))),
    CreateArmyOrder(position=(5, 7)),
    MergeArmyOrder(source_id=8, target_id=2),
    SplitArmyOrder(
        source_id=4, composition=(("soldado", 10), ("arquero", 5))
    ),
    TransferTroopsOrder(
        source_id=6, target_id=1, composition=(("caballero", 3),)
    ),
]


def test_each_order_round_trips():
    for order in ORDERS:
        assert decode_order(encode_order(order)) == order


def test_encoded_order_is_json_serializable():
    import json

    for order in ORDERS:
        # No debe lanzar: solo tipos primitivos en el dict codificado.
        json.dumps(encode_order(order))


def test_move_order_path_keeps_tuples():
    decoded = decode_order(encode_order(MoveOrder(army_id=1, path=((0, 0),))))
    assert isinstance(decoded.path, tuple)
    assert decoded.path == ((0, 0),)
    # Sigue siendo hasheable (frozen dataclass con tuplas).
    hash(decoded)


def test_split_order_composition_keeps_tuples():
    order = SplitArmyOrder(source_id=2, composition=(("caballero", 3),))
    decoded = decode_order(encode_order(order))
    assert decoded == order
    hash(decoded)


def test_orders_list_round_trips():
    assert decode_orders(encode_orders(ORDERS)) == ORDERS


def test_empty_orders_list():
    assert encode_orders([]) == []
    assert decode_orders([]) == []
