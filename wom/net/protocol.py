"""Protocolo de mensajes del multiplayer y framing sobre el stream TCP.

Puro y testeable sin sockets: define el catálogo de mensajes (dataclasses), su
(de)serialización a JSON y el *framing* por longitud para mandarlos por un
stream de bytes. El transporte real (sockets, hilos) vive en `transport.py`.

Cada mensaje se serializa como `{"type": "<Nombre>", ...campos}`. En el cable,
cada mensaje es `uint32 big-endian (longitud) + payload JSON UTF-8`, lo que
permite reconstruir mensajes aunque el stream los parta o los concatene
(`FrameDecoder`).
"""

from __future__ import annotations

import json
import struct
from dataclasses import asdict, dataclass, field

PROTOCOL_VERSION = 1

# Longitud máxima de un frame (16 MiB): cota de cordura contra basura en el
# stream; un STATE_SYNC de un mapa grande entra de sobra.
MAX_FRAME_SIZE = 16 * 1024 * 1024


# --- catálogo de mensajes -------------------------------------------------
# Cada mensaje es una dataclass con campos JSON-serializables (str/int/bool/
# list/dict). Las órdenes y el estado del juego viajan ya codificados a dict
# (ver `orders_codec` y `Game.to_dict`).


@dataclass(frozen=True)
class Hello:
    """cliente→host: se presenta al conectar (handshake)."""

    wom_version: str
    protocol_version: int
    config_hash: str
    name: str


@dataclass(frozen=True)
class Welcome:
    """host/servidor→cliente: acepta o rechaza la conexión.

    En el servidor dedicado, `lobby_id` es el id que el cliente tendrá en el
    lobby (en LAN no aplica y queda en -1)."""

    accepted: bool
    reason: str
    name: str
    lobby_id: int = -1


@dataclass(frozen=True)
class GameSetup:
    """host→cliente: estado inicial completo (`Game.to_dict()`) + reglas.

    `human_id` es el id de jugador que el host le asignó a este cliente (1..N-1;
    el host es 0). Todos los clientes reciben el MISMO `state` autoritativo, solo
    cambia su `human_id`.
    """

    state: dict
    rules: dict
    names: list[str]
    human_id: int = 1


@dataclass(frozen=True)
class Lobby:
    """host→clientes: estado de la sala (id, nombre y listo de cada jugador)."""

    players: list  # [[player_id, name, ready], ...]


@dataclass(frozen=True)
class Ready:
    """ambos: el jugador marca/desmarca que está listo en el lobby."""

    ready: bool


@dataclass(frozen=True)
class Start:
    """host→cliente: ambos listos, arranca el turno 0."""


@dataclass(frozen=True)
class Orders:
    """cliente→host: las órdenes del jugador para un turno (codificadas a dict).
    El host las atribuye al jugador por la conexión de la que llegan."""

    turn: int
    orders: list[dict]


@dataclass(frozen=True)
class TurnOrders:
    """host→clientes: el conjunto completo de órdenes del turno (todas las
    facciones), una vez que el host las juntó. Cada nodo corre exactamente este
    bundle, así el lockstep no puede divergir por el orden de llegada.
    `orders` es una lista de pares `[player_id, [order_dicts]]`."""

    turn: int
    orders: list


@dataclass(frozen=True)
class Hash:
    """ambos: digest del estado tras resolver `turn` (verificación lockstep)."""

    turn: int
    digest: str


@dataclass(frozen=True)
class StateSync:
    """host→cliente: estado autoritativo completo ante un mismatch de Hash."""

    turn: int
    state: dict


@dataclass(frozen=True)
class Chat:
    """ambos: mensaje de chat para el sidebar."""

    name: str
    text: str
    ts: float


# --- zoom de batalla en red (combate táctico en tiempo real) --------------
# La AUTORIDAD (host LAN o servidor) es la única que corre la simulación; el
# resto renderiza desde los snapshots y aplica el BattleResult que difunde la
# autoridad (`BattleEnd`). `battle_id` = índice de la batalla en la cola que
# devuelve `Game.begin_turn` (determinista e idéntico en todos los nodos).


@dataclass(frozen=True)
class BattleOffer:
    """autoridad→todos: hay una batalla con humanos. En modo `"agree"` los
    humanos votan (`BattleVote`); en `"always"` es solo aviso (va directo a
    `BattleBegin`). `human_owners` son los dueños humanos del combate."""

    battle_id: int
    attacker_id: int
    defender_id: int
    mode: str
    human_owners: list


@dataclass(frozen=True)
class BattleBegin:
    """autoridad→todos: arranca el zoom. Cada nodo arma su `TacticalBattle`
    local (determinista desde el estado compartido + `seed`) para renderizar."""

    battle_id: int
    attacker_id: int
    defender_id: int
    human_owners: list
    seed: int


@dataclass(frozen=True)
class BattleSnapshot:
    """autoridad→todos: estado del combate para renderizar (~30 Hz; el cliente
    además interpola entre snapshots para un movimiento fluido).
    `units` = ``[[id, x, y, hp, fled(0/1)], ...]``; `arrows` = ``[[fx,fy,tx,ty],...]``."""

    battle_id: int
    phase: str        # "prep" | "fighting"
    countdown: float
    units: list
    arrows: list


@dataclass(frozen=True)
class BattleEnd:
    """autoridad→todos: la batalla terminó (o se auto-resolvió). `result` es el
    `BattleResult` canónico (dict). Todos lo aplican con `resolve_one_battle`."""

    battle_id: int
    result: dict


@dataclass(frozen=True)
class BattleVote:
    """participante→autoridad: acepta (`zoom=True`) o no dirigir la batalla."""

    battle_id: int
    zoom: bool


@dataclass(frozen=True)
class BattleInput:
    """participante→autoridad: input en vivo del combate.

    `kind`:
    - ``"formation"``: elige formación de despliegue (`formation`).
    - ``"ready"``: el jugador está listo (arranca el combate al estar todos).
    - ``"command"``: orden a un grupo de fichas (`unit_ids`, `order_kind` ∈
      move/attack/hold, `target` = celda [x,y] o id de ficha).
    """

    battle_id: int
    kind: str
    unit_ids: list = field(default_factory=list)
    order_kind: str = ""
    target: list | int | None = None
    formation: str = ""


@dataclass(frozen=True)
class Ping:
    """ambos: keepalive / medición de latencia."""

    t: float


@dataclass(frozen=True)
class Pong:
    """ambos: respuesta a un Ping (eco de su `t`)."""

    t: float


@dataclass(frozen=True)
class Bye:
    """ambos: cierre ordenado de la sesión (host canceló, salida, error)."""

    reason: str


# --- mensajes de lobby (servidor dedicado online) -------------------------
# El servidor dedicado suma una capa de LOBBY por encima de las partidas: los
# jugadores se conectan al servidor (no a un host-jugador), ven el roster y el
# catálogo de partidas, chatean en un canal global y crean o se unen a una
# partida. Una vez DENTRO de una partida se reusan los mensajes de partida de
# arriba (Ready/Start/GameSetup/Orders/TurnOrders/Hash/StateSync/Chat...).


@dataclass(frozen=True)
class Join:
    """cliente→servidor: se presenta al conectar al servidor dedicado.

    Como `Hello`, más la `password` del servidor (vacía si no la exige)."""

    wom_version: str
    protocol_version: int
    config_hash: str
    name: str
    password: str = ""


@dataclass(frozen=True)
class LobbyState:
    """servidor→clientes: foto del lobby. Listas JSON-planas:

    - `players`: ``[[lobby_id, nick, en_partida(bool), match_id(-1 si ninguna)], ...]``
    - `matches`: ``[[match_id, nombre, max_players, ocupados, estado, mapa], ...]``
    """

    players: list
    matches: list


@dataclass(frozen=True)
class CreateMatch:
    """cliente→servidor: crea una partida nueva y la publica en el lobby."""

    name: str
    max_players: int
    map_source: str  # "scenario" | "random"
    map_ref: str = ""  # nombre del .wom si map_source == "scenario"
    rules: dict = field(default_factory=dict)


@dataclass(frozen=True)
class JoinMatch:
    """cliente→servidor: pide unirse a una partida disponible."""

    match_id: int


@dataclass(frozen=True)
class LeaveMatch:
    """cliente→servidor: abandona la sala/partida y vuelve al lobby."""


@dataclass(frozen=True)
class MatchJoined:
    """servidor→cliente: confirma el ingreso a una partida. `seat` es el id de
    jugador que tendrá en esa partida; `roster` es la sala (formato `Lobby`)."""

    match_id: int
    seat: int
    roster: list


@dataclass(frozen=True)
class LobbyChat:
    """ambos: mensaje del chat GLOBAL del lobby (no el chat de una partida)."""

    name: str
    text: str
    ts: float


@dataclass(frozen=True)
class Error:
    """servidor→cliente: una acción fue rechazada. `code` es estable (ver
    `ErrorCode`); `message` es texto listo para el HUD, en español."""

    code: str
    message: str


class ErrorCode:
    """Códigos estables de `Error`: el cliente reacciona según el `code` y
    muestra el `message`. No es un Enum a propósito (viajan como str en JSON)."""

    WRONG_PASSWORD = "WRONG_PASSWORD"
    MATCH_FULL = "MATCH_FULL"
    MATCH_GONE = "MATCH_GONE"
    VERSION_MISMATCH = "VERSION_MISMATCH"
    CONFIG_MISMATCH = "CONFIG_MISMATCH"
    NAME_TAKEN = "NAME_TAKEN"
    RATE_LIMITED = "RATE_LIMITED"
    SERVER_FULL = "SERVER_FULL"
    INVALID = "INVALID"


Message = (
    Hello
    | Welcome
    | GameSetup
    | Lobby
    | Ready
    | Start
    | Orders
    | TurnOrders
    | Hash
    | StateSync
    | Chat
    | Ping
    | Pong
    | Bye
    | BattleOffer
    | BattleBegin
    | BattleSnapshot
    | BattleEnd
    | BattleVote
    | BattleInput
    | Join
    | LobbyState
    | CreateMatch
    | JoinMatch
    | LeaveMatch
    | MatchJoined
    | LobbyChat
    | Error
)

# Registro nombre↔clase para (de)serializar por el campo "type".
_MESSAGE_TYPES: dict[str, type] = {
    cls.__name__: cls
    for cls in (
        Hello,
        Welcome,
        GameSetup,
        Lobby,
        Ready,
        Start,
        Orders,
        TurnOrders,
        Hash,
        StateSync,
        Chat,
        Ping,
        Pong,
        Bye,
        BattleOffer,
        BattleBegin,
        BattleSnapshot,
        BattleEnd,
        BattleVote,
        BattleInput,
        Join,
        LobbyState,
        CreateMatch,
        JoinMatch,
        LeaveMatch,
        MatchJoined,
        LobbyChat,
        Error,
    )
}


# --- (de)serialización ----------------------------------------------------


def to_dict(message: Message) -> dict:
    """Mensaje → dict con el discriminador `type`."""
    return {"type": type(message).__name__, **asdict(message)}


def from_dict(data: dict) -> Message:
    """dict (con `type`) → instancia del mensaje correspondiente."""
    payload = dict(data)
    type_name = payload.pop("type", None)
    cls = _MESSAGE_TYPES.get(type_name)
    if cls is None:
        raise ValueError(f"Tipo de mensaje desconocido: {type_name!r}")
    return cls(**payload)


def encode(message: Message) -> bytes:
    """Mensaje → payload JSON en bytes (sin framing)."""
    return json.dumps(to_dict(message), separators=(",", ":")).encode("utf-8")


def decode(payload: bytes) -> Message:
    """payload JSON en bytes → mensaje (sin framing)."""
    return from_dict(json.loads(payload.decode("utf-8")))


# --- framing por longitud -------------------------------------------------


def encode_frame(message: Message) -> bytes:
    """Mensaje → frame listo para el cable (`uint32 longitud + payload`)."""
    payload = encode(message)
    if len(payload) > MAX_FRAME_SIZE:
        raise ValueError(f"Mensaje demasiado grande: {len(payload)} bytes")
    return struct.pack(">I", len(payload)) + payload


class FrameDecoder:
    """Reensambla mensajes desde un stream de bytes que puede llegar partido.

    Se le van entregando los bytes que va leyendo el socket (`feed`) y devuelve
    los mensajes completos que se hayan podido reconstruir, en orden. Tolera
    que un frame venga partido en varios `feed` o que varios frames lleguen
    juntos en uno.
    """

    def __init__(self) -> None:
        self._buffer = bytearray()

    def feed(self, data: bytes) -> list[Message]:
        """Agrega bytes al buffer y devuelve los mensajes ya completos."""
        self._buffer.extend(data)
        messages: list[Message] = []
        while True:
            if len(self._buffer) < 4:
                break
            (length,) = struct.unpack(">I", self._buffer[:4])
            if length > MAX_FRAME_SIZE:
                raise ValueError(f"Frame declara longitud inválida: {length}")
            if len(self._buffer) < 4 + length:
                break  # frame incompleto: espera más bytes
            payload = bytes(self._buffer[4 : 4 + length])
            del self._buffer[: 4 + length]
            messages.append(decode(payload))
        return messages
