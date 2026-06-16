"""Controlador de la partida en red (lockstep) sobre una `Session`.

Cada cliente tiene su propia copia del `Game` (idéntica, reconstruida del mismo
`to_dict`). Por turno:

1. El humano local arma sus órdenes y las "envía" (`submit_local_orders`): se
   serializan y van al par como `Orders(turn)`; el controlador pasa a ESPERANDO.
2. Cuando tiene **sus** órdenes y las **del par** para el turno en curso, las
   combina en un orden canónico (idéntico en ambos lados) y ejecuta
   `game.run_turn(...)` localmente. Como la simulación es determinista, ambos
   clientes llegan al mismo estado.
3. Tras resolver, intercambian el `Hash` del estado para verificar la sincronía
   (la detección de divergencia está acá; la resincronización por `StateSync`
   es de la fase MP6).

No importa pygame: es testeable headless (dos `NetGame` sobre loopback). La UI
(`GameScreen` en modo red) lo conduce llamando a `update()` una vez por frame y
consume el resultado de cada turno para animarlo (`consume_resolved`).
"""

from __future__ import annotations

from enum import Enum, auto

from wom.core.game import Game
from wom.core.orders import (
    CreateArmyOrder,
    MergeArmyOrder,
    MoveOrder,
    Order,
    SplitArmyOrder,
    TransferTroopsOrder,
)
from wom.core.victory import VictoryResult
from wom.net.orders_codec import decode_orders, encode_orders
from wom.net.session import (
    ChatReceived,
    Disconnected,
    HashReceived,
    OrdersReceived,
    StateSyncReceived,
)
from wom.net.state_hash import state_digest


class Phase(Enum):
    COLLECTING = auto()  # el humano arma sus órdenes
    WAITING = auto()     # órdenes locales enviadas, falta el par
    ENDED = auto()       # fin de partida o desconexión


# Orden canónico de aplicación: ambos clientes deben combinar sus órdenes en
# EXACTAMENTE la misma secuencia, o el resultado de un turno podría diferir
# (p. ej. una fusión y una división que se afectan). Se ordena por tipo y luego
# por id/posición. El tipo manda primero, así reorganizaciones y movimientos no
# se comparan entre sí por su sub-clave.
_KIND_RANK = {
    CreateArmyOrder: 0,
    MergeArmyOrder: 1,
    SplitArmyOrder: 2,
    TransferTroopsOrder: 3,
    MoveOrder: 4,
}


def _order_key(order: Order):
    rank = _KIND_RANK[type(order)]
    if isinstance(order, CreateArmyOrder):
        return (rank, order.position)
    if isinstance(order, MoveOrder):
        return (rank, (order.army_id,))
    if isinstance(order, MergeArmyOrder):
        return (rank, (order.source_id, order.target_id))
    if isinstance(order, SplitArmyOrder):
        return (rank, (order.source_id,))
    return (rank, (order.source_id, order.target_id))  # TransferTroopsOrder


def canonical_order(orders: list[Order]) -> list[Order]:
    """Devuelve las órdenes en el orden canónico de aplicación (determinista)."""
    return sorted(orders, key=_order_key)


class NetGame:
    """Conduce el lockstep de una partida en red para un jugador."""

    def __init__(
        self,
        session,
        game: Game,
        human_id: int,
        is_host: bool,
        peer_name: str = "",
    ) -> None:
        self.session = session
        self.game = game
        self.human_id = human_id
        self.is_host = is_host
        self.peer_name = peer_name
        self.peer_id = next(p.id for p in game.players if p.id != human_id)

        self.phase = Phase.COLLECTING
        self._turn = game.turn  # turno que se está jugando ahora
        self._local_orders: list[Order] | None = None
        self._peer_orders: dict[int, list[Order]] = {}  # turno → órdenes del par
        self._local_hashes: dict[int, str] = {}
        self._peer_hashes: dict[int, str] = {}

        self.chat_log: list[tuple[str, str]] = []
        self.disconnected = False
        self.disconnect_reason = ""
        self.desync = False
        # (resultado, snapshot pre-turno) del último turno resuelto, para que
        # la UI lo anime; lo consume `consume_resolved`.
        self._resolved: tuple[VictoryResult, list[dict]] | None = None

    # --- API que usa la UI -------------------------------------------------

    def submit_local_orders(self, orders: list[Order]) -> None:
        """Finaliza el turno del humano: envía sus órdenes y espera al par."""
        if self.phase is not Phase.COLLECTING:
            return
        self._local_orders = list(orders)
        self.session.send_orders(self._turn, encode_orders(orders))
        self.phase = Phase.WAITING
        self._try_resolve()

    def update(self) -> None:
        """Drena la red y resuelve el turno si ya están las dos listas (1/frame)."""
        if self.phase is Phase.ENDED:
            return
        for event in self.session.update():
            self._handle(event)
        self._try_resolve()

    def consume_resolved(self) -> tuple[VictoryResult, list[dict]] | None:
        """Devuelve (y limpia) el resultado del último turno resuelto, o None."""
        resolved, self._resolved = self._resolved, None
        return resolved

    def send_chat(self, text: str) -> None:
        self.session.send_chat(text)

    @property
    def waiting(self) -> bool:
        return self.phase is Phase.WAITING

    # --- internos ----------------------------------------------------------

    def _handle(self, event) -> None:
        if isinstance(event, OrdersReceived):
            self._peer_orders[event.turn] = decode_orders(event.orders)
        elif isinstance(event, HashReceived):
            self._peer_hashes[event.turn] = event.digest
            self._check_hash(event.turn)
        elif isinstance(event, ChatReceived):
            self.chat_log.append((event.name, event.text))
        elif isinstance(event, StateSyncReceived):
            pass  # resincronización autoritativa: fase MP6
        elif isinstance(event, Disconnected):
            self.disconnected = True
            self.disconnect_reason = event.reason
            self.phase = Phase.ENDED

    def _try_resolve(self) -> None:
        if self.phase is not Phase.WAITING:
            return
        peer = self._peer_orders.get(self._turn)
        if self._local_orders is None or peer is None:
            return
        combined = canonical_order(self._local_orders + self._validate_peer(peer))
        pre_turn = [a.to_dict() for a in self.game.armies]
        result = self.game.run_turn(combined)
        resolved_turn = self.game.turn  # ya incrementado por run_turn

        digest = state_digest(self.game)
        self._local_hashes[resolved_turn] = digest
        self.session.send_hash(resolved_turn, digest)
        self._check_hash(resolved_turn)

        self._resolved = (result, pre_turn)
        self._peer_orders.pop(self._turn, None)
        self._local_orders = None
        self._turn = self.game.turn
        self.phase = Phase.ENDED if result.is_over else Phase.COLLECTING

    def _validate_peer(self, orders: list[Order]) -> list[Order]:
        """Descarta órdenes del par que afecten ejércitos/fuertes que no son
        suyos (defensa ante un cliente con bug o malicioso)."""
        return [o for o in orders if self._owned_by_peer(o)]

    def _owned_by_peer(self, order: Order) -> bool:
        if isinstance(order, CreateArmyOrder):
            fort = self.game.world.fort_at(order.position)
            return fort is not None and fort.owner == self.peer_id
        if isinstance(order, MoveOrder):
            return self._army_owner(order.army_id) == self.peer_id
        if isinstance(order, (SplitArmyOrder,)):
            return self._army_owner(order.source_id) == self.peer_id
        # Merge / Transfer: ambos extremos deben ser del par.
        return (
            self._army_owner(order.source_id) == self.peer_id
            and self._army_owner(order.target_id) == self.peer_id
        )

    def _army_owner(self, army_id: int) -> int | None:
        army = self.game.army_by_id(army_id)
        return army.owner if army is not None else None

    def _check_hash(self, turn: int) -> None:
        local = self._local_hashes.get(turn)
        peer = self._peer_hashes.get(turn)
        if local is not None and peer is not None and local != peer:
            self.desync = True
