"""Controlador de la partida en red (lockstep) sobre una `Session`.

Topología estrella con el host como autoridad (hasta `MAX_PLAYERS`). Cada nodo
tiene su propia copia idéntica del `Game`. Por turno:

1. El humano local arma sus órdenes y las "envía" (`submit_local_orders`). El
   cliente se las manda al host; el host las guarda junto a las suyas.
2. El **host** espera tener las órdenes de TODOS (las suyas + las de cada
   cliente), arma el conjunto completo, lo reparte (`TurnOrders`) y resuelve el
   turno. Cada cliente, al recibir el conjunto (`TurnReady`), corre exactamente
   las mismas órdenes. Como todos aplican el mismo bundle, el lockstep no puede
   divergir por el orden de llegada.
3. Tras resolver, cada cliente le manda su `Hash` al host; el host compara con
   el suyo (autoritativo) y, si difieren, le reenvía su estado (`StateSync`).

El host valida las órdenes de cada rival (que toquen solo lo suyo) antes de
incluirlas en el bundle. No importa pygame: testeable headless. La UI
(`GameScreen` en modo red) lo conduce con `update()` por frame y consume el
resultado de cada turno para animarlo (`consume_resolved`).
"""

from __future__ import annotations

from collections import defaultdict
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
    TurnReady,
)
from wom.net.state_hash import state_digest


class Phase(Enum):
    COLLECTING = auto()  # el humano arma sus órdenes
    WAITING = auto()     # órdenes locales enviadas, falta el resto
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
        # Nombre(s) de los rivales para el HUD: los demás jugadores de la partida.
        self.peer_name = peer_name or ", ".join(
            p.name for p in game.players if p.id != human_id
        )
        self.n_players = len(game.players)

        self.phase = Phase.COLLECTING
        self._turn = game.turn  # turno que se está jugando ahora
        self._local_orders: list[Order] | None = None
        # host: órdenes recibidas de cada rival, por turno → {player_id: [Order]}
        self._client_orders: dict[int, dict[int, list[Order]]] = defaultdict(dict)
        self._local_hashes: dict[int, str] = {}
        # host: hashes recibidos de los clientes, por turno → {player_id: digest}
        self._peer_hashes: dict[int, dict[int, str]] = defaultdict(dict)

        self.chat_log: list[tuple[str, str]] = []
        self.disconnected = False
        self.disconnect_reason = ""
        self.desync = False
        self._sync_sent: set[tuple[int, int]] = set()  # (turn, player_id) ya resincronizados
        self.resynced = False    # el cliente adoptó un estado autoritativo
        # (resultado, snapshot pre-turno) del último turno resuelto, para la UI.
        self._resolved: tuple[VictoryResult, list[dict]] | None = None

    # --- API que usa la UI -------------------------------------------------

    def submit_local_orders(self, orders: list[Order]) -> None:
        """Finaliza el turno del humano: envía sus órdenes y espera al resto."""
        if self.phase is not Phase.COLLECTING:
            return
        self._local_orders = list(orders)
        self.phase = Phase.WAITING
        if self.is_host:
            self._try_resolve_host()
        else:
            self.session.submit_orders(self._turn, encode_orders(orders))

    def update(self) -> None:
        """Drena la red y resuelve el turno si ya están todas las órdenes."""
        if self.phase is Phase.ENDED:
            return
        for event in self.session.update():
            self._handle(event)
        if self.is_host:
            self._try_resolve_host()

    def consume_resolved(self) -> tuple[VictoryResult, list[dict]] | None:
        """Devuelve (y limpia) el resultado del último turno resuelto, o None."""
        resolved, self._resolved = self._resolved, None
        return resolved

    def send_chat(self, text: str) -> None:
        self.session.send_chat(text)
        # El emisor ve su propio mensaje en el mismo log que los recibidos.
        self.chat_log.append((self.game.players[self.human_id].name, text))

    @property
    def waiting(self) -> bool:
        return self.phase is Phase.WAITING

    # --- internos ----------------------------------------------------------

    def _handle(self, event) -> None:
        if isinstance(event, OrdersReceived):  # solo host
            self._client_orders[event.turn][event.player_id] = self._validate_owned(
                decode_orders(event.orders), event.player_id
            )
        elif isinstance(event, TurnReady):  # solo cliente
            self._run_bundle(event.turn, event.bundle)
        elif isinstance(event, HashReceived):  # solo host
            self._peer_hashes[event.turn][event.player_id] = event.digest
            self._check_hash(event.turn, event.player_id)
        elif isinstance(event, ChatReceived):
            self.chat_log.append((event.name, event.text))
        elif isinstance(event, StateSyncReceived):  # solo cliente
            self._apply_state_sync(event.state)
        elif isinstance(event, Disconnected):
            self.disconnected = True
            self.disconnect_reason = event.reason
            self.phase = Phase.ENDED

    def _try_resolve_host(self) -> None:
        """Host: si ya tiene las órdenes propias y las de todos los rivales para
        el turno en curso, arma el bundle, lo reparte y resuelve."""
        if self.phase is not Phase.WAITING or self._local_orders is None:
            return
        client_orders = self._client_orders.get(self._turn, {})
        if len(client_orders) < self.n_players - 1:
            return
        bundle = {self.human_id: self._local_orders, **client_orders}
        self.session.broadcast_turn_orders(
            self._turn, {pid: encode_orders(od) for pid, od in bundle.items()}
        )
        self._apply_turn(self._turn, bundle)
        # Hashes de clientes que pudieron llegar antes de resolver el turno.
        resolved_turn = self._turn  # _apply_turn ya avanzó self._turn
        for player_id in list(self._peer_hashes.get(resolved_turn, {})):
            self._check_hash(resolved_turn, player_id)

    def _run_bundle(self, turn: int, bundle: dict) -> None:
        """Cliente: corre el conjunto completo de órdenes que repartió el host."""
        if self.phase is Phase.ENDED or turn != self._turn:
            return
        decoded = {int(pid): decode_orders(od) for pid, od in bundle.items()}
        self._apply_turn(turn, decoded)

    def _apply_turn(self, turn: int, bundle: dict[int, list[Order]]) -> None:
        combined = canonical_order([o for orders in bundle.values() for o in orders])
        pre_turn = [a.to_dict() for a in self.game.armies]
        result = self.game.run_turn(combined)
        resolved_turn = self.game.turn  # ya incrementado por run_turn

        digest = state_digest(self.game)
        self._local_hashes[resolved_turn] = digest
        if not self.is_host:
            self.session.submit_hash(resolved_turn, digest)

        self._resolved = (result, pre_turn)
        self._client_orders.pop(turn, None)
        self._local_orders = None
        self._turn = self.game.turn
        self.phase = Phase.ENDED if result.is_over else Phase.COLLECTING

    def _validate_owned(self, orders: list[Order], player_id: int) -> list[Order]:
        """Descarta órdenes que afecten ejércitos/fuertes que no son del jugador
        indicado (defensa ante un cliente con bug o malicioso)."""
        return [o for o in orders if self._owned_by(o, player_id)]

    def _owned_by(self, order: Order, player_id: int) -> bool:
        if isinstance(order, CreateArmyOrder):
            fort = self.game.world.fort_at(order.position)
            return fort is not None and fort.owner == player_id
        if isinstance(order, MoveOrder):
            return self._army_owner(order.army_id) == player_id
        if isinstance(order, SplitArmyOrder):
            return self._army_owner(order.source_id) == player_id
        # Merge / Transfer: ambos extremos deben ser del jugador.
        return (
            self._army_owner(order.source_id) == player_id
            and self._army_owner(order.target_id) == player_id
        )

    def _army_owner(self, army_id: int) -> int | None:
        army = self.game.army_by_id(army_id)
        return army.owner if army is not None else None

    def _check_hash(self, turn: int, player_id: int) -> None:
        local = self._local_hashes.get(turn)
        peer = self._peer_hashes.get(turn, {}).get(player_id)
        if local is not None and peer is not None and local != peer:
            self.desync = True
            # El host es la autoridad: reenvía su estado completo al cliente que
            # divergió (red de seguridad ante un bug raro de determinismo). Uno
            # solo por (turno, cliente), para no spamear.
            key = (turn, player_id)
            if self.is_host and key not in self._sync_sent:
                self._sync_sent.add(key)
                self.session.send_state_sync(player_id, self.game.turn, self.game.to_dict())

    def _apply_state_sync(self, state: dict) -> None:
        """Cliente: adopta el estado autoritativo del host y reanuda limpio."""
        if self.is_host:
            return  # el host no se resincroniza con nadie
        self.game.load_state(state)
        self._turn = self.game.turn
        self._local_orders = None
        self._client_orders.clear()
        self.desync = False
        self.resynced = True
        self.phase = Phase.COLLECTING
