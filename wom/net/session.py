"""Sesión de red: handshake, lobby y máquina de estados (host y cliente).

Envuelve una `Connection` y traduce el flujo de mensajes del protocolo en
**eventos de alto nivel** que la UI consume (`update()` se llama una vez por
frame y devuelve la lista de eventos nuevos). Cubre:

- Handshake: el cliente se presenta (`Hello`); el host valida versión, versión
  de protocolo y huella de config, y responde `Welcome` (acepta o rechaza).
- Lobby: ambos marcan "listo" (`Ready`); cuando los dos están listos el host
  envía `Start` y ambos pasan a jugar.
- Juego en curso: envío/recepción de `Orders`, `Hash`, `StateSync` y `Chat`
  (el bucle de turnos en sí lo maneja la capa de UI/NetPlayer, fase MP4).

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
    Orders,
    Ready,
    Start,
    StateSync,
    Welcome,
)
from wom.net.transport import Connection
from wom import __version__


class SessionState(Enum):
    CONNECTING = auto()
    LOBBY = auto()
    PLAYING = auto()
    CLOSED = auto()


# --- eventos de alto nivel para la UI -------------------------------------


@dataclass(frozen=True)
class Connected:
    """Handshake exitoso: se conoce el nombre del par y se entra al lobby."""

    peer_name: str


@dataclass(frozen=True)
class Rejected:
    """El handshake fue rechazado (versión/protocolo/config incompatibles)."""

    reason: str


@dataclass(frozen=True)
class GameReady:
    """El cliente recibió el estado inicial y las reglas (entra al lobby)."""

    setup: GameSetup


@dataclass(frozen=True)
class ReadyChanged:
    """El par marcó/desmarcó "listo" en el lobby."""

    name: str
    ready: bool


@dataclass(frozen=True)
class Started:
    """Ambos listos: arranca la partida (turno 0)."""


@dataclass(frozen=True)
class OrdersReceived:
    turn: int
    orders: list[dict]


@dataclass(frozen=True)
class HashReceived:
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
    """La conexión se cerró (par caído, host canceló, error)."""

    reason: str


Event = (
    Connected
    | Rejected
    | GameReady
    | ReadyChanged
    | Started
    | OrdersReceived
    | HashReceived
    | StateSyncReceived
    | ChatReceived
    | Disconnected
)


class _BaseSession:
    """Lógica común a host y cliente (estado, desconexión, juego, chat)."""

    def __init__(self, connection: Connection, name: str) -> None:
        self.connection = connection
        self.name = name
        self.peer_name: str | None = None
        self.state = SessionState.CONNECTING
        self.local_ready = False
        self.peer_ready = False

    # --- API de envío (host y cliente) ---------------------------------

    def send_orders(self, turn: int, orders: list[dict]) -> None:
        self.connection.send(Orders(turn=turn, orders=orders))

    def send_hash(self, turn: int, digest: str) -> None:
        self.connection.send(Hash(turn=turn, digest=digest))

    def send_chat(self, text: str) -> None:
        self.connection.send(Chat(name=self.name, text=text, ts=time.time()))

    def set_ready(self, value: bool) -> None:
        """Marca el estado de "listo" local y se lo informa al par."""
        self.local_ready = value
        self.connection.send(Ready(ready=value))

    def cancel(self, reason: str = "cerrada por el usuario") -> None:
        """Cierra la sesión avisando al par (Bye) y corta la conexión."""
        if self.state is not SessionState.CLOSED:
            self.connection.send(Bye(reason=reason))
        self._close()

    def _close(self) -> None:
        self.state = SessionState.CLOSED
        self.connection.close()

    # --- bucle de actualización ----------------------------------------

    def update(self) -> list[Event]:
        """Procesa los mensajes recibidos y devuelve los eventos nuevos."""
        events: list[Event] = []
        if self.state is SessionState.CLOSED:
            return events
        # Se drena la cola primero: aunque la conexión ya haya muerto puede
        # quedar un Bye (cierre ordenado) por procesar, que tiene prioridad
        # sobre el aviso genérico de caída.
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
        elif isinstance(message, Chat):
            events.append(ChatReceived(message.name, message.text, message.ts))
        elif isinstance(message, Orders):
            events.append(OrdersReceived(message.turn, message.orders))
        elif isinstance(message, Hash):
            events.append(HashReceived(message.turn, message.digest))
        elif isinstance(message, StateSync):
            events.append(StateSyncReceived(message.turn, message.state))
        else:
            self._handle_role(message, events)

    def _handle_role(self, message, events: list[Event]) -> None:
        raise NotImplementedError


class HostSession(_BaseSession):
    """Lado host (player 0): valida el handshake y arbitra el arranque.

    `setup_provider(peer_name) -> GameSetup` construye el estado inicial y las
    reglas una vez que se conoce el nombre del cliente (para nombrar a ambos
    jugadores). Se mantiene fuera de `net` para no acoplar la red a cómo se
    arma la partida.
    """

    def __init__(
        self,
        connection: Connection,
        name: str,
        setup_provider: Callable[[str], GameSetup],
        config_hash: str | None = None,
    ) -> None:
        super().__init__(connection, name)
        self._setup_provider = setup_provider
        self._config_hash = config_hash if config_hash is not None else config_fingerprint()
        self._setup: GameSetup | None = None

    def _handle_role(self, message, events: list[Event]) -> None:
        if isinstance(message, Hello) and self.state is SessionState.CONNECTING:
            self._handle_hello(message, events)
        elif isinstance(message, Ready) and self.state is SessionState.LOBBY:
            self.peer_ready = message.ready
            events.append(ReadyChanged(self.peer_name or "", message.ready))

    def _handle_hello(self, hello: Hello, events: list[Event]) -> None:
        reason = self._reject_reason(hello)
        if reason is not None:
            self.connection.send(Welcome(accepted=False, reason=reason, name=self.name))
            events.append(Disconnected(reason))
            self._close()
            return
        self.peer_name = hello.name
        self._setup = self._setup_provider(hello.name)
        self.connection.send(Welcome(accepted=True, reason="", name=self.name))
        self.connection.send(self._setup)
        self.state = SessionState.LOBBY
        events.append(Connected(hello.name))

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

    def update(self) -> list[Event]:
        # El host arbitra el arranque: tras procesar mensajes (que pueden traer
        # el Ready del par) y/o un set_ready local, si ambos están listos manda
        # Start y pasa a jugar.
        events = super().update()
        if self.local_ready and self.peer_ready and self.state is SessionState.LOBBY:
            self.connection.send(Start())
            self.state = SessionState.PLAYING
            events.append(Started())
        return events

    def send_state_sync(self, turn: int, state: dict) -> None:
        """Solo el host es autoritativo: reenvía el estado ante un mismatch."""
        self.connection.send(StateSync(turn=turn, state=state))


class ClientSession(_BaseSession):
    """Lado cliente (player 1): se presenta y espera setup + arranque."""

    def __init__(
        self,
        connection: Connection,
        name: str,
        config_hash: str | None = None,
    ) -> None:
        super().__init__(connection, name)
        self.setup: GameSetup | None = None
        chash = config_hash if config_hash is not None else config_fingerprint()
        # Se presenta apenas se construye la sesión (handshake).
        connection.send(
            Hello(
                wom_version=__version__,
                protocol_version=PROTOCOL_VERSION,
                config_hash=chash,
                name=name,
            )
        )

    def _handle_role(self, message, events: list[Event]) -> None:
        if isinstance(message, Welcome):
            if message.accepted:
                self.peer_name = message.name
                events.append(Connected(message.name))
            else:
                events.append(Rejected(message.reason))
                self._close()
        elif isinstance(message, GameSetup):
            self.setup = message
            self.state = SessionState.LOBBY
            events.append(GameReady(message))
        elif isinstance(message, Ready) and self.state is SessionState.LOBBY:
            self.peer_ready = message.ready
            events.append(ReadyChanged(self.peer_name or "", message.ready))
        elif isinstance(message, Start) and self.state is SessionState.LOBBY:
            self.state = SessionState.PLAYING
            events.append(Started())
