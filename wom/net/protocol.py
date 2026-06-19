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
from dataclasses import asdict, dataclass

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
    """host→cliente: acepta o rechaza la conexión."""

    accepted: bool
    reason: str
    name: str


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
