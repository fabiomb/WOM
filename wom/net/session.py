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
    BattleBegin,
    BattleEnd,
    BattleInput,
    BattleOffer,
    BattleSnapshot,
    BattleVote,
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


# --- eventos del zoom de batalla en red -----------------------------------
# La autoridad difunde Offer/Begin/Snapshot/End (los reciben los clientes); los
# participantes mandan Vote/Input (los recibe la autoridad). El evento envuelve
# el mensaje del protocolo tal cual; `NetGame`/`MatchRunner` lo interpretan.


@dataclass(frozen=True)
class BattleOfferReceived:
    """cliente: la autoridad ofrece dirigir una batalla (modo agree)."""

    message: BattleOffer


@dataclass(frozen=True)
class BattleBeginReceived:
    """cliente: arranca el zoom de una batalla."""

    message: BattleBegin


@dataclass(frozen=True)
class BattleSnapshotReceived:
    """cliente: estado del combate para renderizar."""

    message: BattleSnapshot


@dataclass(frozen=True)
class BattleEndReceived:
    """cliente: la batalla terminó (resultado o auto)."""

    message: BattleEnd


@dataclass(frozen=True)
class BattleVoteReceived:
    """host: un participante votó dirigir/auto."""

    player_id: int
    message: BattleVote


@dataclass(frozen=True)
class BattleInputReceived:
    """host: input en vivo de un participante."""

    player_id: int
    message: BattleInput


@dataclass(frozen=True)
class PlayerLeft:
    """host: un rival se cayó en plena partida. El slot queda libre para que
    vuelva; mientras tanto la IA lo controla (lo decide el `NetGame`)."""

    player_id: int
    reason: str


@dataclass(frozen=True)
class PlayerRejoined:
    """host: un rival que se había caído volvió a la partida (retoma su lugar)."""

    player_id: int
    name: str


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
    | BattleOfferReceived
    | BattleBeginReceived
    | BattleSnapshotReceived
    | BattleEndReceived
    | BattleVoteReceived
    | BattleInputReceived
    | PlayerLeft
    | PlayerRejoined
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
        server=None,
    ) -> None:
        self.n_players = n_players
        self.name = name
        self.state = SessionState.CONNECTING
        self._setup_provider = setup_provider
        self._config_hash = config_hash if config_hash is not None else config_fingerprint()
        self.local_ready = False
        self._server = server  # si está, update() saca de él las conexiones nuevas
        self._seen_conns: set = set()
        self._pending: list[Connection] = []      # conexiones sin Hello aún
        self._clients: dict[int, _ClientLink] = {}  # player_id → link
        # Jugadores caídos: player_id → último nombre. El slot queda reservado
        # para que vuelvan (reconexión); mientras tanto la IA los controla.
        self._absent: dict[int, str] = {}
        self._next_id = 1
        self._setup_sent = False
        self._base_setup: GameSetup | None = None  # setup inicial (para rejoins en lobby)
        # Lo setea el NetGame del host: () -> (estado_vivo, nombres), para armar
        # el GameSetup de un jugador que reconecta en plena partida.
        self.live_state_provider: Callable[[], tuple[dict, list[str]]] | None = None
        self.peer_name = ""  # sin uso en el host (lo deriva NetGame)

    # --- alta de conexiones / consultas ---------------------------------

    def add_connection(self, conn: Connection) -> None:
        """Registra una conexión entrante (aún sin handshake). Idempotente: cada
        conexión se procesa una sola vez."""
        if self.state is SessionState.CLOSED:
            conn.close()
            return
        if conn in self._seen_conns:
            return
        self._seen_conns.add(conn)
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

    def broadcast_battle(self, message) -> None:
        """Difunde un mensaje del zoom de batalla (Offer/Begin/Snapshot/End)."""
        self._broadcast(message)

    def send_chat(self, text: str) -> None:
        """Chat del host: a todos los clientes (el host lo ve por NetGame)."""
        self._broadcast(Chat(name=self.name, text=text, ts=time.time()))

    def send_system_chat(self, text: str) -> None:
        """Aviso del sistema (caída/regreso de un jugador) a todos los clientes."""
        self._broadcast(Chat(name="Sistema", text=text, ts=time.time()))

    def cancel(self, reason: str = "cerrada por el host") -> None:
        """Cierra la sesión avisando a todos (Bye) y corta las conexiones."""
        if self.state is not SessionState.CLOSED:
            self._broadcast(Bye(reason=reason))
        for link in self._clients.values():
            link.conn.close()
        for conn in self._pending:
            conn.close()
        if self._server is not None:
            self._server.close()
        self.state = SessionState.CLOSED

    # --- bucle de actualización -----------------------------------------

    def update(self) -> list[Event]:
        events: list[Event] = []
        if self.state is SessionState.CLOSED:
            return events
        if self._server is not None:
            for conn in self._server.poll_connections():
                self.add_connection(conn)
        self._poll_pending(events)
        for player_id, link in list(self._clients.items()):
            if player_id not in self._clients:
                continue  # se cayó por Bye en este mismo pase
            for message in link.conn.poll():
                self._handle_client(player_id, link, message, events)
                if self.state is SessionState.CLOSED:
                    return events
                if player_id not in self._clients:
                    break
            if player_id in self._clients and not link.conn.alive:
                self._drop_client(player_id, link.conn.error or "conexión perdida", events)
        self._maybe_send_setup()
        self._maybe_start(events)
        return events

    def _drop_client(self, player_id: int, reason: str, events: list[Event]) -> None:
        """Saca a un cliente caído pero NO cierra la sesión: deja el slot
        reservado (ausente) para que vuelva; la IA lo controla mientras tanto."""
        link = self._clients.pop(player_id, None)
        if link is None:
            return
        link.conn.close()
        self._absent[player_id] = link.name
        events.append(PlayerLeft(player_id, reason))
        self._broadcast_lobby()

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
        reason = self._reject_reason(hello)
        if reason is not None:
            conn.send(Welcome(accepted=False, reason=reason, name=self.name))
            conn.close()
            return
        if self._absent:  # reconexión: retoma el slot de un jugador caído
            self._readmit(conn, hello, events)
            return
        if len(self._clients) >= self.n_players - 1:
            conn.send(Welcome(accepted=False, reason="la partida está llena", name=self.name))
            conn.close()
            return
        player_id = self._next_id
        self._next_id += 1
        self._clients[player_id] = _ClientLink(conn, hello.name)
        conn.send(Welcome(accepted=True, reason="", name=self.name))
        events.append(Connected(player_id, hello.name))
        self._broadcast_lobby()

    def _readmit(self, conn: Connection, hello: Hello, events: list[Event]) -> None:
        """Readmite a un jugador que se había caído (id ausente más bajo)."""
        player_id = min(self._absent)
        self._absent.pop(player_id)
        self._clients[player_id] = _ClientLink(conn, hello.name)
        conn.send(Welcome(accepted=True, reason="", name=self.name))
        if self.state is SessionState.PLAYING and self.live_state_provider is not None:
            # En plena partida: arranca directo con el estado vivo del host.
            state, names = self.live_state_provider()
            rules = self._base_setup.rules if self._base_setup is not None else {}
            conn.send(GameSetup(state=state, rules=rules, names=names, human_id=player_id))
            conn.send(Start())
            events.append(PlayerRejoined(player_id, hello.name))
        elif self._setup_sent and self._base_setup is not None:
            # Volvió durante el lobby: reenvía el setup inicial.
            base = self._base_setup
            conn.send(
                GameSetup(state=base.state, rules=base.rules, names=base.names, human_id=player_id)
            )
            events.append(Connected(player_id, hello.name))
        else:
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
            # Salida ordenada de un cliente: como una caída, deja el slot libre.
            self._drop_client(player_id, message.reason, events)
        elif isinstance(message, Ready) and self.state is SessionState.LOBBY:
            link.ready = message.ready
            self._broadcast_lobby()
        elif isinstance(message, Orders):
            events.append(OrdersReceived(player_id, message.turn, message.orders))
        elif isinstance(message, Hash):
            events.append(HashReceived(player_id, message.turn, message.digest))
        elif isinstance(message, BattleVote):
            events.append(BattleVoteReceived(player_id, message))
        elif isinstance(message, BattleInput):
            events.append(BattleInputReceived(player_id, message))
        elif isinstance(message, Chat):
            events.append(ChatReceived(message.name, message.text, message.ts))
            self._relay_chat(message, exclude=player_id)

    def _maybe_send_setup(self) -> None:
        """Cuando están todos los rivales, arma el estado y se lo manda a cada uno."""
        if self._setup_sent or len(self._clients) < self.n_players - 1:
            return
        base = self._setup_provider(self.names)
        self._base_setup = base
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

    def send_battle_vote(self, battle_id: int, zoom: bool) -> None:
        self.connection.send(BattleVote(battle_id=battle_id, zoom=zoom))

    def send_battle_input(self, message: BattleInput) -> None:
        self.connection.send(message)

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
        elif isinstance(message, BattleOffer):
            events.append(BattleOfferReceived(message))
        elif isinstance(message, BattleBegin):
            events.append(BattleBeginReceived(message))
        elif isinstance(message, BattleSnapshot):
            events.append(BattleSnapshotReceived(message))
        elif isinstance(message, BattleEnd):
            events.append(BattleEndReceived(message))
        elif isinstance(message, Chat):
            events.append(ChatReceived(message.name, message.text, message.ts))
