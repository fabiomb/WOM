"""Transporte TCP del multiplayer: conexión, hilo lector y aceptación.

`Connection` envuelve un socket conectado: un hilo lector reensambla los
mensajes (`FrameDecoder`) y los deja en una cola que el loop de la app drena
una vez por frame (`poll`), así la red nunca bloquea el render. `send` es
thread-safe. Cuando el par cierra o hay error de red, `alive` pasa a False.

`Server` escucha en segundo plano y acepta hasta `max_clients` conexiones (una
por cada rival que se une a la partida del host). `connect` es el lado cliente.
Nada de esto importa pygame.
"""

from __future__ import annotations

import queue
import socket
import threading

from wom.net.protocol import FrameDecoder, Message, encode_frame

DEFAULT_PORT = 50000
_RECV_CHUNK = 4096


class Connection:
    """Un socket conectado con un hilo lector que entrega mensajes por cola."""

    def __init__(self, sock: socket.socket) -> None:
        self._sock = sock
        self._sock.settimeout(None)  # bloqueante: el hilo lector espera datos
        self._decoder = FrameDecoder()
        self._inbox: "queue.Queue[Message]" = queue.Queue()
        self._send_lock = threading.Lock()
        self._alive = threading.Event()
        self._alive.set()
        self._error: str | None = None
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()

    @property
    def alive(self) -> bool:
        """False cuando el par cerró la conexión o hubo un error de red."""
        return self._alive.is_set()

    @property
    def error(self) -> str | None:
        """Mensaje del último error de red, si lo hubo."""
        return self._error

    def _read_loop(self) -> None:
        try:
            while True:
                data = self._sock.recv(_RECV_CHUNK)
                if not data:
                    break  # el par cerró ordenadamente (EOF)
                for message in self._decoder.feed(data):
                    self._inbox.put(message)
        except (OSError, ValueError) as exc:
            self._error = str(exc)
        finally:
            self._alive.clear()

    def send(self, message: Message) -> bool:
        """Envía un mensaje. Devuelve False si la conexión ya no está viva."""
        if not self._alive.is_set():
            return False
        frame = encode_frame(message)
        try:
            with self._send_lock:
                self._sock.sendall(frame)
            return True
        except OSError as exc:
            self._error = str(exc)
            self._alive.clear()
            return False

    def poll(self) -> list[Message]:
        """Devuelve y vacía los mensajes recibidos hasta el momento."""
        messages: list[Message] = []
        while True:
            try:
                messages.append(self._inbox.get_nowait())
            except queue.Empty:
                break
        return messages

    def close(self) -> None:
        """Cierra el socket y marca la conexión como muerta (idempotente)."""
        self._alive.clear()
        try:
            self._sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            self._sock.close()
        except OSError:
            pass


class Server:
    """Escucha en segundo plano y acepta hasta `max_clients` conexiones."""

    def __init__(
        self, host: str = "0.0.0.0", port: int = DEFAULT_PORT, max_clients: int = 1
    ) -> None:
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind((host, port))
        self._sock.listen(max(1, max_clients))
        self._max_clients = max(1, max_clients)
        self._connections: list[Connection] = []
        self._lock = threading.Lock()
        self._open = True
        self._thread = threading.Thread(target=self._accept_loop, daemon=True)
        self._thread.start()

    @property
    def port(self) -> int:
        """Puerto efectivo (útil con port=0, que deja elegir uno libre al SO)."""
        return self._sock.getsockname()[1]

    def _accept_loop(self) -> None:
        # Acepta mientras el server esté abierto (no se corta al llenarse): así
        # un jugador que se cayó puede reconectarse durante la partida. Quién
        # entra y quién se rechaza lo decide la sesión (slots libres/ausentes).
        while True:
            try:
                client, _addr = self._sock.accept()
            except OSError:
                return  # se cerró el server mientras esperaba
            with self._lock:
                if self._open:
                    self._connections.append(Connection(client))
                else:
                    client.close()
                    return

    def poll_connections(self) -> list[Connection]:
        """Todas las conexiones aceptadas hasta el momento (copia de la lista)."""
        with self._lock:
            return list(self._connections)

    def poll_connection(self) -> Connection | None:
        """La primera conexión aceptada (o None). Atajo para el caso de 1 rival."""
        with self._lock:
            return self._connections[0] if self._connections else None

    def close(self) -> None:
        """Deja de aceptar y cierra el socket de escucha (no las conexiones ya
        aceptadas, que son responsabilidad de quien las consumió)."""
        with self._lock:
            self._open = False
        try:
            self._sock.close()
        except OSError:
            pass


def connect(host: str, port: int, timeout: float = 10.0) -> Connection:
    """Lado cliente: conecta a un host y devuelve la `Connection`.

    Lanza `OSError` si no puede conectar dentro de `timeout` segundos.
    """
    sock = socket.create_connection((host, port), timeout=timeout)
    return Connection(sock)
