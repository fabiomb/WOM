"""Sesión de red: handshake, lobby y máquina de estados (host y cliente).

Topología **estrella con el host como relay/autoridad** (hasta `MAX_PLAYERS`):
el host (jugador 0) acepta una conexión por cada rival, les asigna un id
(1..N-1), arma el estado inicial autoritativo y arbitra el arranque. Los
clientes solo hablan con el host.

- Handshake: cada cliente se presenta (`Hello`); el host valida versión,
  versión de protocolo y huella de config, y responde `Welcome` (acepta o
  rechaza). Cuando están todos los rivales, el host manda a cada uno su
  `GameSetup` (mismo estado, distinto `human_id`).
- Lobby: todos marcan "listo" (`Ready`); el host difunde la sala (`Lobby`) y,
  cuando todos están listos, envía `Start` y la partida arranca.
- Juego: cada cliente manda sus `Orders` al host; el host las junta con las
  propias y reparte el conjunto completo (`TurnOrders`) a todos. Hash/StateSync
  y Chat también pasan por el host.

Estados: ``CONNECTING → LOBBY → PLAYING → CLOSED``. No importa pygame.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from enum import Enum, auto
from typing import Callable

from wom.net.config_fingerprint import config_fingerprint
from wom.net.protocol import (
    PROTOCOL_VERSION,
    Bye,
    Chat,
    GameSetup,
    Hash,
    Hello,
    Lobby,
    Orders,
    Ready,
    Start,
    StateSync,
    TurnOrders,
    Welcome,
)
from wom.net.transport import Connection
from wom import __version__


class SessionState(Enum):
    CONNECTING = auto()
    LOBBY = auto()
    PLAYING = auto()
    CLOSED = auto()


# --- eventos de alto nivel para la UI / NetGame ----------------------------


@dataclass(frozen=True)
class Connected:
    """Handshake exitoso. En el host: `player_id` es el id asignado al rival que
    entró; en el cliente: el id es 0 (el host) y `name` su nombre."""

    player_id: int
    name: str


@dataclass(frozen=True)
class Rejected:
    """El handshake fue rechazado (versión/protocolo/config/llena)."""

    reason: str


@dataclass(frozen=True)
class GameReady:
    """El cliente recibió el estado inicial y las reglas (entra al lobby)."""

    setup: GameSetup


@dataclass(frozen=True)
class Started:
    """Todos listos: arranca la partida (turno 0)."""


@dataclass(frozen=True)
class OrdersReceived:
    """host: llegaron las órdenes de un rival para un turno."""

    player_id: int
    turn: int
    orders: list[dict]


@dataclass(frozen=True)
class TurnReady:
    """cliente: el host repartió el conjunto completo de órdenes del turno.
    `bundle` es {player_id: [order_dicts]}."""

    turn: int
    bundle: dict


@dataclass(frozen=True)
class HashReceived:
    player_id: int
    turn: int
    digest: str


@dataclass(frozen=True)
class StateSyncReceived:
    turn: int
    state: dict


@dataclass(frozen=True)
class ChatReceived:
    name: str
    text: str
    ts: float


@dataclass(frozen=True)
class Disconnected:
    """La conexión se cerró (par caído, host canceló, error). `player_id` indica
    qué rival cayó (host); None si fue la propia conexión del cliente."""

    reason: str
    player_id: int | None = None


Event = (
    Connected
    | Rejected
    | GameReady
    | Started
    | OrdersReceived
    | TurnReady
    | HashReceived
    | StateSyncReceived
    | ChatReceived
    | Disconnected
)


@dataclass
class _ClientLink:
    """Un rival conectado al host: su conexión, nombre y estado de listo."""

    conn: Connection
    name: str
    ready: bool = False


class HostSession:
    """Lado host (jugador 0): valida handshakes, arma el lobby y hace de relay.

    `setup_provider(names) -> GameSetup` construye el estado inicial una vez que
    se conocen los nombres de todos los jugadores (host primero). Se mantiene
    fuera de `net` para no acoplar la red a cómo se arma la partida. Las
    conexiones entrantes se le entregan con `add_connection` (la UI las saca del
    `Server`).
    """

    def __init__(
        self,
        n_players: int,
        name: str,
        setup_provider: Callable[[list[str]], GameSetup],
        config_hash: str | None = None,
    ) -> None:
        self.n_players = n_players
        self.name = name
        self.state = SessionState.CONNECTING
        self._setup_provider = setup_provider
        self._config_hash = config_hash if config_hash is not None else config_fingerprint()
        self.local_ready = False
        self._pending: list[Connection] = []      # conexiones sin Hello aún
        self._clients: dict[int, _ClientLink] = {}  # player_id → link
        self._next_id = 1
        self._setup_sent = False
        self.peer_name = ""  # sin uso en el host (lo deriva NetGame)

    # --- alta de conexiones / consultas ---------------------------------

    def add_connection(self, conn: Connection) -> None:
        """Registra una conexión entrante (aún sin handshake). Idempotente: una
        conexión ya pendiente o ya promovida a cliente no se vuelve a agregar."""
        if self.state is SessionState.CLOSED:
            conn.close()
            return
        if conn in self._pending or any(link.conn is conn for link in self._clients.values()):
            return
        self._pending.append(conn)

    @property
    def names(self) -> list[str]:
        """Nombres ordenados por id: host primero, rivales por id de conexión."""
        return [self.name] + [self._clients[p].name for p in sorted(self._clients)]

    def roster(self) -> list[tuple[int, str, bool]]:
        """Estado de la sala: (id, nombre, listo) por jugador."""
        rows = [(0, self.name, self.local_ready)]
        rows += [(p, self._clients[p].name, self._clients[p].ready) for p in sorted(self._clients)]
        return rows

    # --- API de envío ----------------------------------------------------

    def set_ready(self, value: bool) -> None:
        self.local_ready = value
        self._broadcast_lobby()

    def broadcast_turn_orders(self, turn: int, bundle: dict[int, list[dict]]) -> None:
        """Reparte a todos los clientes el conjunto completo de órdenes del turno."""
        self._broadcast(TurnOrders(turn=turn, orders=[[p, od] for p, od in bundle.items()]))

    def send_state_sync(self, player_id: int, turn: int, state: dict) -> None:
        """Reenvía el estado autoritativo a un cliente que divergió."""
        link = self._clients.get(player_id)
        if link is not None:
            link.conn.send(StateSync(turn=turn, state=state))

    def send_chat(self, text: str) -> None:
        """Chat del host: a todos los clientes (el host lo ve por NetGame)."""
        self._broadcast(Chat(name=self.name, text=text, ts=time.time()))

    def cancel(self, reason: str = "cerrada por el host") -> None:
        """Cierra la sesión avisando a todos (Bye) y corta las conexiones."""
        if self.state is not SessionState.CLOSED:
            self._broadcast(Bye(reason=reason))
        for link in self._clients.values():
            link.conn.close()
        for conn in self._pending:
            conn.close()
        self.state = SessionState.CLOSED

    # --- bucle de actualización -----------------------------------------

    def update(self) -> list[Event]:
        events: list[Event] = []
        if self.state is SessionState.CLOSED:
            return events
        self._poll_pending(events)
        for player_id, link in list(self._clients.items()):
            for message in link.conn.poll():
                self._handle_client(player_id, link, message, events)
                if self.state is SessionState.CLOSED:
                    return events
            if not link.conn.alive and self.state is not SessionState.CLOSED:
                events.append(Disconnected(link.conn.error or "conexión perdida", player_id))
                self.cancel("un jugador se desconectó")
                return events
        self._maybe_send_setup()
        self._maybe_start(events)
        return events

    def _poll_pending(self, events: list[Event]) -> None:
        for conn in list(self._pending):
            if not conn.alive:
                self._pending.remove(conn)
                continue
            for message in conn.poll():
                if isinstance(message, Hello):
                    self._accept(conn, message, events)
                    break

    def _accept(self, conn: Connection, hello: Hello, events: list[Event]) -> None:
        self._pending.remove(conn)
        if len(self._clients) >= self.n_players - 1:
            conn.send(Welcome(accepted=False, reason="la partida está llena", name=self.name))
            conn.close()
            return
        reason = self._reject_reason(hello)
        if reason is not None:
            conn.send(Welcome(accepted=False, reason=reason, name=self.name))
            conn.close()
            return
        player_id = self._next_id
        self._next_id += 1
        self._clients[player_id] = _ClientLink(conn, hello.name)
        conn.send(Welcome(accepted=True, reason="", name=self.name))
        events.append(Connected(player_id, hello.name))
        self._broadcast_lobby()

    def _reject_reason(self, hello: Hello) -> str | None:
        if hello.protocol_version != PROTOCOL_VERSION:
            return (
                f"versión de protocolo incompatible "
                f"(host {PROTOCOL_VERSION}, cliente {hello.protocol_version})"
            )
        if hello.wom_version != __version__:
            return f"versión de WOM distinta (host {__version__}, cliente {hello.wom_version})"
        if hello.config_hash != self._config_hash:
            return "la configuración de balance no coincide entre los jugadores"
        return None

    def _handle_client(
        self, player_id: int, link: _ClientLink, message, events: list[Event]
    ) -> None:
        if isinstance(message, Bye):
            events.append(Disconnected(message.reason, player_id))
            self.cancel("un jugador salió de la partida")
        elif isinstance(message, Ready) and self.state is SessionState.LOBBY:
            link.ready = message.ready
            self._broadcast_lobby()
        elif isinstance(message, Orders):
            events.append(OrdersReceived(player_id, message.turn, message.orders))
        elif isinstance(message, Hash):
            events.append(HashReceived(player_id, message.turn, message.digest))
        elif isinstance(message, Chat):
            events.append(ChatReceived(message.name, message.text, message.ts))
            self._relay_chat(message, exclude=player_id)

    def _maybe_send_setup(self) -> None:
        """Cuando están todos los rivales, arma el estado y se lo manda a cada uno."""
        if self._setup_sent or len(self._clients) < self.n_players - 1:
            return
        base = self._setup_provider(self.names)
        for player_id, link in self._clients.items():
            link.conn.send(
                GameSetup(
                    state=base.state,
                    rules=base.rules,
                    names=base.names,
                    human_id=player_id,
                )
            )
        self._setup_sent = True
        self.state = SessionState.LOBBY
        self._broadcast_lobby()

    def _maybe_start(self, events: list[Event]) -> None:
        if self.state is not SessionState.LOBBY:
            return
        if not self.local_ready or len(self._clients) != self.n_players - 1:
            return
        if any(not link.ready for link in self._clients.values()):
            return
        self._broadcast(Start())
        self.state = SessionState.PLAYING
        events.append(Started())

    def _broadcast(self, message) -> None:
        for link in self._clients.values():
            link.conn.send(message)

    def _broadcast_lobby(self) -> None:
        self._broadcast(Lobby(players=[list(row) for row in self.roster()]))

    def _relay_chat(self, message: Chat, exclude: int) -> None:
        for player_id, link in self._clients.items():
            if player_id != exclude:
                link.conn.send(message)


class ClientSession:
    """Lado cliente (jugador 1..N-1): se presenta y sigue al host."""

    def __init__(
        self,
        connection: Connection,
        name: str,
        config_hash: str | None = None,
    ) -> None:
        self.connection = connection
        self.name = name
        self.state = SessionState.CONNECTING
        self.peer_name: str | None = None  # nombre del host
        self.human_id: int | None = None
        self.setup: GameSetup | None = None
        self.local_ready = False
        self._roster: list[tuple[int, str, bool]] = []
        chash = config_hash if config_hash is not None else config_fingerprint()
        connection.send(
            Hello(
                wom_version=__version__,
                protocol_version=PROTOCOL_VERSION,
                config_hash=chash,
                name=name,
            )
        )

    def roster(self) -> list[tuple[int, str, bool]]:
        return list(self._roster)

    # --- API de envío ----------------------------------------------------

    def set_ready(self, value: bool) -> None:
        self.local_ready = value
        self.connection.send(Ready(ready=value))

    def submit_orders(self, turn: int, orders: list[dict]) -> None:
        self.connection.send(Orders(turn=turn, orders=orders))

    def submit_hash(self, turn: int, digest: str) -> None:
        self.connection.send(Hash(turn=turn, digest=digest))

    def send_chat(self, text: str) -> None:
        self.connection.send(Chat(name=self.name, text=text, ts=time.time()))

    def cancel(self, reason: str = "cerrada por el usuario") -> None:
        if self.state is not SessionState.CLOSED:
            self.connection.send(Bye(reason=reason))
        self._close()

    def _close(self) -> None:
        self.state = SessionState.CLOSED
        self.connection.close()

    # --- bucle de actualización -----------------------------------------

    def update(self) -> list[Event]:
        events: list[Event] = []
        if self.state is SessionState.CLOSED:
            return events
        for message in self.connection.poll():
            self._handle(message, events)
            if self.state is SessionState.CLOSED:
                break
        if self.state is not SessionState.CLOSED and not self.connection.alive:
            events.append(Disconnected(self.connection.error or "conexión perdida"))
            self.state = SessionState.CLOSED
        return events

    def _handle(self, message, events: list[Event]) -> None:
        if isinstance(message, Bye):
            events.append(Disconnected(message.reason))
            self._close()
        elif isinstance(message, Welcome):
            if message.accepted:
                self.peer_name = message.name
                events.append(Connected(0, message.name))
            else:
                events.append(Rejected(message.reason))
                self._close()
        elif isinstance(message, GameSetup):
            self.setup = message
            self.human_id = message.human_id
            self.state = SessionState.LOBBY
            events.append(GameReady(message))
        elif isinstance(message, Lobby):
            self._roster = [tuple(row) for row in message.players]
        elif isinstance(message, Start) and self.state is SessionState.LOBBY:
            self.state = SessionState.PLAYING
            events.append(Started())
        elif isinstance(message, TurnOrders):
            bundle = {int(p): od for p, od in message.orders}
            events.append(TurnReady(message.turn, bundle))
        elif isinstance(message, StateSync):
            events.append(StateSyncReceived(message.turn, message.state))
        elif isinstance(message, Chat):
            events.append(ChatReceived(message.name, message.text, message.ts))
