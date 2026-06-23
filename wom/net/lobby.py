"""Lobby del servidor dedicado: handshake, roster, chat global y catálogo de
partidas.

Es la capa que el **servidor dedicado** suma por encima de las partidas (ver
`docs/server.md`). A diferencia del host LAN (`HostSession`), acá la autoridad no
es un jugador: el `LobbyServer` recibe a todos los que se conectan, los mantiene
en un **lobby** con chat global, y publica un **catálogo de partidas** que cada
uno puede crear o a las que puede unirse. Una vez DENTRO de una partida, su
ciclo de vida (readies, setup, lockstep) lo lleva el `MatchRunner` (fase S3): el
lobby solo enruta hacia él los mensajes de partida (`MatchMessage`) y administra
la ocupación de los asientos.

Opera sobre `Connection`s (igual que `HostSession`), no toca sockets directo, y
**no importa pygame** (lo verifica el test de humo). Es testeable headless con
pares de `Connection` en loopback o con dobles.

Estados de una conexión: ``pendiente (sin Join) → en lobby → en partida``. Los
rechazos de handshake (versión/protocolo/config/contraseña/lleno/nick) se avisan
con `Welcome(accepted=False)`; los rechazos de acciones del lobby (partida llena,
inexistente, etc.) con `Error(code, message)`.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable

from wom.core.worldmap import MAX_PLAYERS
from wom.net.config_fingerprint import config_fingerprint
from wom.net.protocol import (
    PROTOCOL_VERSION,
    Bye,
    CreateMatch,
    Error,
    ErrorCode,
    Join,
    JoinMatch,
    LeaveMatch,
    LobbyChat,
    LobbyState,
    MatchJoined,
    Welcome,
)
from wom.net.transport import Connection
from wom import __version__

# Topes de cordura (validación de entrada; el anti-DDOS fino es la fase S5).
MAX_NAME_LEN = 24
MAX_CHAT_LEN = 240

# Asiento de una partida EN CURSO cuyo humano se fue: queda reservado (lo cubre
# la IA) hasta que alguien reconecte y lo retome.
ABSENT_SEAT = -1


# --- spec de una partida del catálogo -------------------------------------


@dataclass(frozen=True)
class MatchSpec:
    """Lo que define una partida al crearse (sin estado de juego todavía)."""

    name: str
    max_players: int
    map_source: str  # "scenario" | "random"
    map_ref: str = ""  # nombre del .wom si map_source == "scenario"
    rules: dict = field(default_factory=dict)

    def map_label(self) -> str:
        return self.map_ref if self.map_source == "scenario" and self.map_ref else "aleatorio"


# --- eventos de alto nivel (los consume el servidor / la capa de partidas) -


@dataclass(frozen=True)
class PlayerEntered:
    """Un jugador completó el handshake y entró al lobby."""

    lobby_id: int
    name: str


@dataclass(frozen=True)
class PlayerExited:
    """Un jugador se desconectó del servidor (salió o se cayó)."""

    lobby_id: int
    name: str


@dataclass(frozen=True)
class MatchOpened:
    """Se creó una partida nueva (el creador queda en el asiento 0)."""

    match_id: int
    creator_id: int
    spec: MatchSpec


@dataclass(frozen=True)
class SeatTaken:
    """Un jugador ocupó un asiento de una partida (al crearla o unirse)."""

    match_id: int
    lobby_id: int
    seat: int


@dataclass(frozen=True)
class SeatFreed:
    """Un jugador dejó un asiento en una sala que AÚN NO empezó (queda libre
    para otro)."""

    match_id: int
    lobby_id: int
    seat: int


@dataclass(frozen=True)
class SeatVacated:
    """Un jugador dejó un asiento de una partida EN CURSO: el asiento queda
    reservado y el `MatchRunner` lo cubre con IA hasta una reconexión."""

    match_id: int
    lobby_id: int
    seat: int


@dataclass(frozen=True)
class SeatRejoined:
    """Un jugador reconectó y retomó un asiento reservado de una partida en
    curso: el `MatchRunner` le manda el estado vivo y suelta la IA."""

    match_id: int
    lobby_id: int
    seat: int


@dataclass(frozen=True)
class MatchClosed:
    """Una partida se quedó sin jugadores y se recicla del catálogo."""

    match_id: int


@dataclass(frozen=True)
class MatchMessage:
    """Un mensaje de PARTIDA (Ready/Orders/Hash/Chat de partida...) que llegó de
    un jugador que está en una partida: lo enruta el `MatchRunner` (fase S3)."""

    match_id: int
    seat: int
    lobby_id: int
    message: object


Event = (
    PlayerEntered
    | PlayerExited
    | MatchOpened
    | SeatTaken
    | SeatFreed
    | SeatVacated
    | SeatRejoined
    | MatchClosed
    | MatchMessage
)


# --- estructuras internas --------------------------------------------------


@dataclass
class _LobbyClient:
    conn: Connection
    lobby_id: int
    name: str
    match_id: int | None = None  # partida en la que está, o None (libre)


@dataclass
class _Match:
    match_id: int
    spec: MatchSpec
    seats: dict[int, int] = field(default_factory=dict)  # seat → lobby_id
    playing: bool = False

    @property
    def occupied(self) -> int:
        return len(self.seats)

    @property
    def full(self) -> bool:
        return self.occupied >= self.spec.max_players

    def state_label(self) -> str:
        if self.playing:
            return "en curso"
        return "llena" if self.full else "abierta"

    def free_seat(self) -> int:
        """Menor asiento libre (0..max-1)."""
        for seat in range(self.spec.max_players):
            if seat not in self.seats:
                return seat
        raise ValueError("partida llena")  # no debería pasar: se chequea antes

    def to_row(self) -> list:
        return [
            self.match_id,
            self.spec.name,
            self.spec.max_players,
            self.occupied,
            self.state_label(),
            self.spec.map_label(),
        ]


class LobbyServer:
    """Lobby + catálogo de partidas del servidor dedicado.

    `setup`/`config_hash` definen el handshake. Las conexiones entrantes se le
    entregan con `add_connection`; si se le pasa un `server` (`transport.Server`),
    `update()` las saca solo. `time_fn` se inyecta para testear el timeout de
    handshake.
    """

    def __init__(
        self,
        *,
        name: str = "Servidor WOM",
        config_hash: str | None = None,
        require_password: bool = False,
        password: str = "",
        handshake_timeout: float = 10.0,
        max_connections: int = 200,
        max_matches: int = 20,
        allow_random: bool = True,
        server=None,
        time_fn: Callable[[], float] = time.monotonic,
    ) -> None:
        self.name = name
        self._config_hash = config_hash if config_hash is not None else config_fingerprint()
        self._require_password = require_password
        self._password = password
        self._handshake_timeout = handshake_timeout
        self._max_connections = max_connections
        self._max_matches = max_matches
        self._allow_random = allow_random
        self._server = server
        self._now = time_fn

        self._seen: set = set()
        self._pending: dict[Connection, float] = {}  # conexión → deadline
        self._clients: dict[int, _LobbyClient] = {}   # lobby_id → cliente
        self._matches: dict[int, _Match] = {}
        self._next_lobby_id = 0
        self._next_match_id = 1

    # --- alta de conexiones / consultas ----------------------------------

    def add_connection(self, conn: Connection) -> None:
        """Registra una conexión entrante (aún sin Join). Idempotente."""
        if conn in self._seen:
            return
        self._seen.add(conn)
        self._pending[conn] = self._now() + self._handshake_timeout

    @property
    def player_count(self) -> int:
        return len(self._clients)

    @property
    def match_count(self) -> int:
        return len(self._matches)

    def match_spec(self, match_id: int) -> MatchSpec | None:
        match = self._matches.get(match_id)
        return match.spec if match is not None else None

    def name_of(self, lobby_id: int) -> str:
        """Nombre de un jugador conectado (vacío si no está)."""
        cli = self._clients.get(lobby_id)
        return cli.name if cli is not None else ""

    def seats_of(self, match_id: int) -> dict[int, int]:
        """seat → lobby_id de una partida (copia)."""
        match = self._matches.get(match_id)
        return dict(match.seats) if match is not None else {}

    def players_snapshot(self) -> list[list]:
        """Filas de jugadores para `LobbyState` (orden estable por id)."""
        rows = []
        for lobby_id in sorted(self._clients):
            cli = self._clients[lobby_id]
            rows.append([lobby_id, cli.name, cli.match_id is not None, cli.match_id if cli.match_id is not None else -1])
        return rows

    def matches_snapshot(self) -> list[list]:
        return [self._matches[mid].to_row() for mid in sorted(self._matches)]

    # --- API que usa la capa de partidas / el runnable -------------------

    def send_to(self, lobby_id: int, message) -> bool:
        """Manda un mensaje a un jugador puntual (lo usa el MatchRunner)."""
        cli = self._clients.get(lobby_id)
        return cli.conn.send(message) if cli is not None else False

    def broadcast_to_match(self, match_id: int, message) -> None:
        """Manda un mensaje a todos los ocupantes de una partida."""
        match = self._matches.get(match_id)
        if match is None:
            return
        for lobby_id in match.seats.values():
            self.send_to(lobby_id, message)

    def mark_match_playing(self, match_id: int, playing: bool = True) -> None:
        """La capa de partidas marca una sala como en curso (o la reabre)."""
        match = self._matches.get(match_id)
        if match is not None:
            match.playing = playing
            self._broadcast_lobby_state()

    def close_match(self, match_id: int) -> None:
        """La capa de partidas recicla una partida terminada y libera a sus
        jugadores (vuelven al lobby)."""
        match = self._matches.pop(match_id, None)
        if match is None:
            return
        for lobby_id in match.seats.values():
            cli = self._clients.get(lobby_id)
            if cli is not None and cli.match_id == match_id:
                cli.match_id = None
        self._broadcast_lobby_state()

    def shutdown(self, reason: str = "el servidor se está cerrando") -> None:
        """Cierra todas las conexiones avisando con Bye."""
        for cli in self._clients.values():
            cli.conn.send(Bye(reason=reason))
            cli.conn.close()
        for conn in self._pending:
            conn.close()
        self._clients.clear()
        self._pending.clear()

    # --- bucle de actualización ------------------------------------------

    def update(self) -> list[Event]:
        events: list[Event] = []
        if self._server is not None:
            for conn in self._server.poll_connections():
                self.add_connection(conn)
        self._poll_pending(events)
        for lobby_id in list(self._clients):
            cli = self._clients.get(lobby_id)
            if cli is None:
                continue
            for message in cli.conn.poll():
                self._handle(cli, message, events)
                if lobby_id not in self._clients:
                    break
            cli = self._clients.get(lobby_id)
            if cli is not None and not cli.conn.alive:
                self._drop_client(lobby_id, events)
        return events

    # --- handshake -------------------------------------------------------

    def _poll_pending(self, events: list[Event]) -> None:
        now = self._now()
        for conn in list(self._pending):
            if not conn.alive:
                self._pending.pop(conn, None)
                continue
            handled = False
            for message in conn.poll():
                if isinstance(message, Join):
                    self._accept(conn, message, events)
                    handled = True
                    break
            if handled:
                continue
            if now >= self._pending.get(conn, now):
                # No se presentó a tiempo: cierra (anti conexiones zombi).
                conn.close()
                self._pending.pop(conn, None)

    def _accept(self, conn: Connection, join: Join, events: list[Event]) -> None:
        self._pending.pop(conn, None)
        reason = self._reject_reason(join)
        if reason is not None:
            conn.send(Welcome(accepted=False, reason=reason, name=self.name))
            conn.close()
            return
        lobby_id = self._next_lobby_id
        self._next_lobby_id += 1
        name = join.name.strip()
        self._clients[lobby_id] = _LobbyClient(conn, lobby_id, name)
        conn.send(Welcome(accepted=True, reason="", name=self.name, lobby_id=lobby_id))
        events.append(PlayerEntered(lobby_id, name))
        self._broadcast_lobby_state()

    def _reject_reason(self, join: Join) -> str | None:
        if join.protocol_version != PROTOCOL_VERSION:
            return (
                f"versión de protocolo incompatible (servidor {PROTOCOL_VERSION}, "
                f"cliente {join.protocol_version})"
            )
        if join.wom_version != __version__:
            return f"versión de WOM distinta (servidor {__version__}, cliente {join.wom_version})"
        if join.config_hash != self._config_hash:
            return "la configuración de balance no coincide con la del servidor"
        if self._require_password and join.password != self._password:
            return "contraseña incorrecta"
        if len(self._clients) >= self._max_connections:
            return "el servidor está lleno"
        name = join.name.strip()
        if not name or len(name) > MAX_NAME_LEN:
            return "nombre inválido"
        if any(cli.name.lower() == name.lower() for cli in self._clients.values()):
            return "ese nombre ya está en uso"
        return None

    # --- mensajes del lobby ----------------------------------------------

    def _handle(self, cli: _LobbyClient, message, events: list[Event]) -> None:
        if isinstance(message, Bye):
            self._drop_client(cli.lobby_id, events)
        elif isinstance(message, LobbyChat):
            self._relay_lobby_chat(cli, message)
        elif isinstance(message, CreateMatch):
            self._create_match(cli, message, events)
        elif isinstance(message, JoinMatch):
            self._join_match(cli, message.match_id, events)
        elif isinstance(message, LeaveMatch):
            self._leave_match(cli, events)
        elif cli.match_id is not None:
            # Cualquier otro mensaje de un jugador en partida es de PARTIDA:
            # se lo damos al MatchRunner (fase S3) vía evento.
            seat = self._seat_of(cli)
            events.append(MatchMessage(cli.match_id, seat, cli.lobby_id, message))

    def _relay_lobby_chat(self, cli: _LobbyClient, message: LobbyChat) -> None:
        text = message.text[:MAX_CHAT_LEN]
        out = LobbyChat(name=cli.name, text=text, ts=message.ts)
        for other in self._clients.values():
            other.conn.send(out)

    def _create_match(self, cli: _LobbyClient, msg: CreateMatch, events: list[Event]) -> None:
        if cli.match_id is not None:
            self._error(cli, ErrorCode.INVALID, "ya estás en una partida")
            return
        if len(self._matches) >= self._max_matches:
            self._error(cli, ErrorCode.SERVER_FULL, "no se pueden crear más partidas ahora")
            return
        reason = self._validate_spec(msg)
        if reason is not None:
            self._error(cli, ErrorCode.INVALID, reason)
            return
        match_id = self._next_match_id
        self._next_match_id += 1
        spec = MatchSpec(
            name=(msg.name.strip() or f"Partida {match_id}")[:MAX_NAME_LEN],
            max_players=msg.max_players,
            map_source=msg.map_source,
            map_ref=msg.map_ref,
            rules=dict(msg.rules),
        )
        match = _Match(match_id=match_id, spec=spec, seats={0: cli.lobby_id})
        self._matches[match_id] = match
        cli.match_id = match_id
        cli.conn.send(MatchJoined(match_id=match_id, seat=0, roster=self._roster(match)))
        self._broadcast_lobby_state()
        events.append(MatchOpened(match_id, cli.lobby_id, spec))
        events.append(SeatTaken(match_id, cli.lobby_id, 0))

    def _validate_spec(self, msg: CreateMatch) -> str | None:
        if not (2 <= msg.max_players <= MAX_PLAYERS):
            return f"cantidad de jugadores inválida (2 a {MAX_PLAYERS})"
        if msg.map_source not in ("scenario", "random"):
            return "origen de mapa inválido"
        if msg.map_source == "random" and not self._allow_random:
            return "el servidor no permite mapas aleatorios"
        if msg.map_source == "scenario" and not msg.map_ref:
            return "falta el escenario"
        return None

    def _join_match(self, cli: _LobbyClient, match_id: int, events: list[Event]) -> None:
        if cli.match_id is not None:
            self._error(cli, ErrorCode.INVALID, "ya estás en una partida")
            return
        match = self._matches.get(match_id)
        if match is None:
            self._error(cli, ErrorCode.MATCH_GONE, "esa partida ya no existe")
            return
        if match.playing:
            # En curso: solo se puede entrar a un asiento reservado (reconexión).
            absent = [s for s, lid in match.seats.items() if lid == ABSENT_SEAT]
            if not absent:
                self._error(cli, ErrorCode.MATCH_FULL, "la partida está completa")
                return
            seat = min(absent)
            match.seats[seat] = cli.lobby_id
            cli.match_id = match_id
            cli.conn.send(MatchJoined(match_id=match_id, seat=seat, roster=self._roster(match)))
            self._broadcast_lobby_state()
            events.append(SeatRejoined(match_id, cli.lobby_id, seat))
            return
        if match.full:
            self._error(cli, ErrorCode.MATCH_FULL, "la partida está llena")
            return
        seat = match.free_seat()
        match.seats[seat] = cli.lobby_id
        cli.match_id = match_id
        cli.conn.send(MatchJoined(match_id=match_id, seat=seat, roster=self._roster(match)))
        self._broadcast_lobby_state()
        events.append(SeatTaken(match_id, cli.lobby_id, seat))

    def _leave_match(self, cli: _LobbyClient, events: list[Event]) -> None:
        if cli.match_id is None:
            return
        self._release_seat(cli, events)
        self._broadcast_lobby_state()

    # --- bajas / liberación de asientos ----------------------------------

    def _drop_client(self, lobby_id: int, events: list[Event]) -> None:
        cli = self._clients.get(lobby_id)
        if cli is None:
            return
        self._release_seat(cli, events)
        cli.conn.close()
        self._clients.pop(lobby_id, None)
        events.append(PlayerExited(lobby_id, cli.name))
        self._broadcast_lobby_state()

    def _release_seat(self, cli: _LobbyClient, events: list[Event]) -> None:
        if cli.match_id is None:
            return
        match = self._matches.get(cli.match_id)
        match_id = cli.match_id
        cli.match_id = None
        if match is None:
            return
        seat = next((s for s, lid in match.seats.items() if lid == cli.lobby_id), None)
        if seat is None:
            return
        if match.playing:
            # En curso: el asiento NO se libera; queda reservado (lo cubre la IA)
            # hasta una reconexión. Si no queda ningún humano conectado, se cierra.
            match.seats[seat] = ABSENT_SEAT
            events.append(SeatVacated(match_id, cli.lobby_id, seat))
            humans = [lid for lid in match.seats.values() if lid != ABSENT_SEAT and lid in self._clients]
            if not humans:
                self._matches.pop(match_id, None)
                events.append(MatchClosed(match_id))
            return
        del match.seats[seat]
        events.append(SeatFreed(match_id, cli.lobby_id, seat))
        if not match.seats:
            self._matches.pop(match_id, None)
            events.append(MatchClosed(match_id))

    # --- helpers ----------------------------------------------------------

    def _seat_of(self, cli: _LobbyClient) -> int:
        match = self._matches.get(cli.match_id)
        if match is None:
            return -1
        return next((s for s, lid in match.seats.items() if lid == cli.lobby_id), -1)

    def _roster(self, match: _Match) -> list:
        """Sala de una partida: [[seat, nombre], ...] ordenada por asiento.
        Un asiento reservado (sin humano, lo cubre la IA) aparece como "(IA)"."""
        rows = []
        for seat in sorted(match.seats):
            lid = match.seats[seat]
            cli = self._clients.get(lid)
            if lid == ABSENT_SEAT or cli is None:
                rows.append([seat, "(IA)"])
            else:
                rows.append([seat, cli.name])
        return rows

    def _error(self, cli: _LobbyClient, code: str, message: str) -> None:
        cli.conn.send(Error(code=code, message=message))

    def _broadcast_lobby_state(self) -> None:
        state = LobbyState(players=self.players_snapshot(), matches=self.matches_snapshot())
        for cli in self._clients.values():
            cli.conn.send(state)
