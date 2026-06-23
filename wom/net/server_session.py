"""Sesión del cliente contra el servidor dedicado (lado jugador).

Es el análogo de `ClientSession` (LAN) pero para el **servidor dedicado**: en vez
de hablar con un host-jugador, el cliente se conecta al servidor, entra a un
**lobby** (roster + chat global + catálogo de partidas), crea o se une a una
partida y, una vez dentro, sigue el mismo lockstep que en LAN (los mensajes de
partida `GameSetup`/`Start`/`TurnOrders`/`StateSync` se reusan tal cual).

La UI (navegador de servidores, fase S6; vista de lobby, S7) la conduce con
`update()` por frame y traduce sus eventos a estado de pantalla. No importa
pygame: testeable headless con un `LobbyServer` en loopback.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from enum import Enum, auto

from wom.net.config_fingerprint import config_fingerprint
from wom.net.protocol import (
    PROTOCOL_VERSION,
    Bye,
    Chat,
    CreateMatch,
    Error,
    GameSetup,
    Hash,
    Join,
    JoinMatch,
    LeaveMatch,
    LobbyChat,
    LobbyState,
    MatchJoined,
    Orders,
    Ready,
    Start,
    StateSync,
    TurnOrders,
    Welcome,
)
# Los eventos de la FASE DE PARTIDA se reusan de `session` tal cual los espera el
# `NetGame` del cliente (drop-in: la partida en red por servidor usa el mismo
# lockstep que LAN). Los eventos de LOBBY son propios de este módulo.
from wom.net.session import ChatReceived, Disconnected, StateSyncReceived, TurnReady
from wom import __version__


class ServerState(Enum):
    CONNECTING = auto()  # mandó Join, espera Welcome
    LOBBY = auto()       # admitido: ve roster, chat y partidas
    IN_MATCH = auto()    # recibió el estado inicial de una partida
    CLOSED = auto()


# --- eventos para la UI ----------------------------------------------------


@dataclass(frozen=True)
class Connected:
    server_name: str
    lobby_id: int


@dataclass(frozen=True)
class Rejected:
    reason: str


@dataclass(frozen=True)
class LobbyUpdated:
    players: list
    matches: list


@dataclass(frozen=True)
class ErrorReceived:
    code: str
    message: str


@dataclass(frozen=True)
class LobbyChatReceived:
    name: str
    text: str
    ts: float


@dataclass(frozen=True)
class MatchJoinedEvent:
    match_id: int
    seat: int
    roster: list


@dataclass(frozen=True)
class GameReady:
    setup: GameSetup


@dataclass(frozen=True)
class Started:
    pass


# Fase de partida: Disconnected/TurnReady/StateSyncReceived/ChatReceived vienen
# de `wom.net.session` (los consume el NetGame del cliente).
Event = (
    Connected
    | Rejected
    | Disconnected
    | LobbyUpdated
    | ErrorReceived
    | LobbyChatReceived
    | MatchJoinedEvent
    | GameReady
    | Started
    | TurnReady
    | StateSyncReceived
    | ChatReceived
)


class ServerSession:
    """Cliente de un servidor dedicado: handshake, lobby y partida."""

    def __init__(
        self,
        connection,
        name: str,
        *,
        password: str = "",
        config_hash: str | None = None,
    ) -> None:
        self.connection = connection
        self.name = name
        self.state = ServerState.CONNECTING
        self.server_name = ""
        self.lobby_id: int | None = None
        self.players: list = []   # roster del lobby (LobbyState.players)
        self.matches: list = []   # catálogo de partidas (LobbyState.matches)
        self.match_id: int | None = None
        self.seat: int | None = None
        self.match_roster: list = []
        self.setup: GameSetup | None = None
        chash = config_hash if config_hash is not None else config_fingerprint()
        connection.send(
            Join(
                wom_version=__version__,
                protocol_version=PROTOCOL_VERSION,
                config_hash=chash,
                name=name,
                password=password,
            )
        )

    # --- API de envío ----------------------------------------------------

    def send_lobby_chat(self, text: str) -> None:
        self.connection.send(LobbyChat(name=self.name, text=text, ts=time.time()))

    def create_match(
        self, name: str, max_players: int, map_source: str, map_ref: str = "", rules: dict | None = None
    ) -> None:
        self.connection.send(
            CreateMatch(
                name=name, max_players=max_players, map_source=map_source,
                map_ref=map_ref, rules=rules or {},
            )
        )

    def join_match(self, match_id: int) -> None:
        self.connection.send(JoinMatch(match_id=match_id))

    def leave_match(self) -> None:
        self.connection.send(LeaveMatch())

    def set_ready(self, value: bool) -> None:
        self.connection.send(Ready(ready=value))

    def send_chat(self, text: str) -> None:
        """Chat DENTRO de una partida (lo usa el NetGame del cliente)."""
        self.connection.send(Chat(name=self.name, text=text, ts=time.time()))

    def submit_orders(self, turn: int, orders: list[dict]) -> None:
        self.connection.send(Orders(turn=turn, orders=orders))

    def submit_hash(self, turn: int, digest: str) -> None:
        self.connection.send(Hash(turn=turn, digest=digest))

    def cancel(self, reason: str = "salida del jugador") -> None:
        if self.state is not ServerState.CLOSED:
            self.connection.send(Bye(reason=reason))
        self._close()

    def _close(self) -> None:
        self.state = ServerState.CLOSED
        self.connection.close()

    # --- bucle de actualización ------------------------------------------

    def update(self) -> list[Event]:
        events: list[Event] = []
        if self.state is ServerState.CLOSED:
            return events
        for message in self.connection.poll():
            self._handle(message, events)
            if self.state is ServerState.CLOSED:
                break
        if self.state is not ServerState.CLOSED and not self.connection.alive:
            events.append(Disconnected(self.connection.error or "conexión perdida"))
            self.state = ServerState.CLOSED
        return events

    def _handle(self, message, events: list[Event]) -> None:
        if isinstance(message, Welcome):
            if message.accepted:
                self.server_name = message.name
                self.lobby_id = message.lobby_id
                self.state = ServerState.LOBBY
                events.append(Connected(message.name, message.lobby_id))
            else:
                events.append(Rejected(message.reason))
                self._close()
        elif isinstance(message, Bye):
            events.append(Disconnected(message.reason))
            self._close()
        elif isinstance(message, LobbyState):
            self.players = message.players
            self.matches = message.matches
            events.append(LobbyUpdated(message.players, message.matches))
        elif isinstance(message, Error):
            events.append(ErrorReceived(message.code, message.message))
        elif isinstance(message, LobbyChat):
            events.append(LobbyChatReceived(message.name, message.text, message.ts))
        elif isinstance(message, Chat):  # chat DENTRO de una partida
            events.append(ChatReceived(message.name, message.text, message.ts))
        elif isinstance(message, MatchJoined):
            self.match_id = message.match_id
            self.seat = message.seat
            self.match_roster = message.roster
            events.append(MatchJoinedEvent(message.match_id, message.seat, message.roster))
        elif isinstance(message, GameSetup):
            self.setup = message
            self.seat = message.human_id
            self.state = ServerState.IN_MATCH
            events.append(GameReady(message))
        elif isinstance(message, Start):
            events.append(Started())
        elif isinstance(message, TurnOrders):
            bundle = {int(p): od for p, od in message.orders}
            events.append(TurnReady(message.turn, bundle))
        elif isinstance(message, StateSync):
            events.append(StateSyncReceived(message.turn, message.state))
