"""`LLMRunner`: el rival LLM corriendo en un hilo dentro del propio juego.

Es la versión embebida de `tools/llm_client.py` para el flujo **Multijugador →
Jugar contra AI LLM**: el humano hostea una partida en loopback (127.0.0.1) y
este hilo se conecta como cliente de red normal (`ClientSession` + `NetGame` +
`LLMPlayer`), así reutiliza todo el lockstep determinista, el roster y el chat
sin ningún camino especial. No importa pygame (va en el smoke test).

Aporta dos cosas que el CLI no tiene:

- **Estado observable para la UI** (`status`, `thinking_since`): la pantalla de
  juego muestra "El LLM está pensando… Xs" mientras el backend genera la movida,
  para que el usuario sepa que no se cayó la conexión (según el modelo puede
  tardar mucho).
- **Chat**: cuando llega un mensaje del humano, el runner le pide al backend una
  respuesta corta (con las últimas líneas de la conversación como contexto) y la
  manda por el chat de la partida — el mismo que usan las personas. El chat nunca
  toca la simulación: solo produce mensajes, el lockstep sigue igual.

`probe_backend(config)` es el "test de configuración" del menú: llama al backend
con un prompt trivial y devuelve una descripción, o levanta `LLMError`.
"""

from __future__ import annotations

import threading
import time

from wom.core.game import Game
from wom.llm.agent import LLMPlayer
from wom.llm.backend import BackendConfig, LLMError, make_backend
from wom.net.lockstep import NetGame, Phase
from wom.net.session import (
    ClientSession,
    Connected,
    Disconnected,
    GameReady,
    Rejected,
    Started,
)
from wom.net.transport import connect

_POLL = 0.05  # segundos entre vueltas del loop cuando no hay nada que hacer
_CHAT_CONTEXT = 12  # últimas líneas de chat que ve el modelo al responder


def probe_backend(config: BackendConfig) -> str:
    """Prueba la configuración con una llamada real y mínima al modelo.

    Devuelve una línea de éxito ("<backend>: <respuesta>"); si algo falla
    (proveedor desconocido, sin key, red, HTTP) levanta `LLMError`.
    """
    backend = make_backend(config)
    reply = backend.complete(
        "Sos un asistente de prueba. Respondé en una sola palabra.",
        "Respondé exactamente: OK",
    )
    snippet = " ".join(reply.split())[:60] or "(respuesta vacía)"
    return f"{backend.describe()} → {snippet}"


def chat_reply_prompt(name: str, history: list[tuple[str, str]]) -> tuple[str, str]:
    """(system, user) para responder el chat de la partida. Puro y testeable."""
    system = (
        f"Sos «{name}», un jugador de WOM (juego de estrategia militar por turnos) "
        "charlando por el chat de la partida con tu rival humano. Respondé al último "
        "mensaje de forma breve (1 o 2 frases), amistosa y con espíritu competitivo, "
        "en el mismo idioma del mensaje. Respondé SOLO con el texto del mensaje, sin "
        "comillas ni prefijos."
    )
    lines = [f"{who}: {text}" for who, text in history[-_CHAT_CONTEXT:]]
    user = "Chat de la partida:\n" + "\n".join(lines) + "\n\nTu respuesta:"
    return system, user


class LLMRunner:
    """Conduce al rival LLM como cliente de red en un hilo propio.

    La UI lo crea junto con el host loopback, lo arranca con `start()` y lee
    `status`/`thinking_since`/`error` cada frame (atributos simples: con el GIL
    alcanza para este uso). `stop()` corta el loop y cierra la conexión; también
    termina solo si el host cierra la partida (Disconnected) o el juego acaba.
    """

    def __init__(
        self,
        config: BackendConfig | None,
        name: str = "LLM",
        host: str = "127.0.0.1",
        port: int = 0,
        debug: bool = False,
        backend=None,  # inyectable en tests (si no, se arma de `config`)
    ) -> None:
        self.config = config
        self._backend = backend
        self.name = name or "LLM"
        self.host = host
        self.port = port
        self.debug = debug
        # Estado observable por la UI.
        self.status = "iniciando"
        self.thinking_since: float | None = None  # time.monotonic() al empezar a pensar
        self.error = ""
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._run, name="llm-runner", daemon=True
        )
        self._session: ClientSession | None = None

    # --- ciclo de vida -----------------------------------------------------

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    @property
    def alive(self) -> bool:
        return self._thread.is_alive()

    @property
    def thinking(self) -> bool:
        return self.thinking_since is not None

    def thinking_seconds(self, now: float | None = None) -> float:
        """Segundos que lleva generando la movida actual (0 si no piensa)."""
        since = self.thinking_since
        if since is None:
            return 0.0
        return max(0.0, (now if now is not None else time.monotonic()) - since)

    # --- loop del hilo -----------------------------------------------------

    def _run(self) -> None:
        try:
            backend = self._backend if self._backend is not None else make_backend(self.config)
            player = LLMPlayer(1, backend, debug=self.debug, logger=self._log)
        except LLMError as exc:
            self._fail(f"backend: {exc}")
            return
        try:
            conn = connect(self.host, self.port, timeout=5.0)
        except OSError as exc:
            self._fail(f"no se pudo conectar al host local: {exc}")
            return
        self._session = ClientSession(conn, self.name)
        try:
            game = self._lobby(self._session)
            if game is not None:
                self._play(self._session, game, player, backend)
        finally:
            if self._session.connection is not None:
                self._session.connection.close()
            self.thinking_since = None
            if not self.error:
                self.status = "terminado"

    def _lobby(self, session: ClientSession) -> Game | None:
        """Handshake + sala: se marca listo solo y espera el arranque."""
        self.status = "conectando"
        setup = None
        while not self._stop.is_set():
            for event in session.update():
                if isinstance(event, Connected):
                    self.status = "en la sala"
                elif isinstance(event, Rejected):
                    self._fail(f"rechazado: {event.reason}")
                    return None
                elif isinstance(event, GameReady):
                    setup = event.setup
                    session.set_ready(True)
                elif isinstance(event, Started):
                    return Game.from_dict(setup.state)
                elif isinstance(event, Disconnected):
                    self._fail(f"desconectado: {event.reason}")
                    return None
            time.sleep(_POLL)
        return None

    def _play(self, session, game: Game, player: LLMPlayer, backend) -> None:
        """Lockstep: órdenes por turno + respuestas de chat entre medio."""
        human_id = session.human_id if session.human_id is not None else 1
        player.player_id = human_id
        net = NetGame(session, game, human_id=human_id, is_host=False)
        chat_seen = len(net.chat_log)
        while not self._stop.is_set() and net.phase is not Phase.ENDED:
            net.update()
            if net.phase is Phase.COLLECTING:
                self.status = "pensando"
                self.thinking_since = time.monotonic()
                try:
                    orders = player.decide_orders(net.game)
                finally:
                    self.thinking_since = None
                self.status = "esperando"
                net.submit_local_orders(orders)
            chat_seen = self._answer_chat(net, backend, chat_seen)
            net.consume_resolved()
            time.sleep(_POLL)
        if net.disconnected:
            self.status = f"desconectado: {net.disconnect_reason}"

    def _answer_chat(self, net: NetGame, backend, seen: int) -> int:
        """Responde (una sola vez) a los mensajes nuevos del rival humano."""
        log = net.chat_log
        if seen >= len(log):
            return seen
        fresh = log[seen:]
        seen = len(log)
        own = net.game.players[net.human_id].name
        if not any(who not in (own, self.name, "Sistema") for who, _ in fresh):
            return seen  # solo eco propio o mensajes de sistema: nada que contestar
        system, user = chat_reply_prompt(self.name, list(log))
        try:
            reply = " ".join(backend.complete(system, user).split()).strip()
        except LLMError as exc:
            self._log(f"chat sin respuesta: {exc}")
            return len(net.chat_log)
        if reply:
            net.send_chat(reply[:200])
        return len(net.chat_log)

    def _fail(self, message: str) -> None:
        self.error = message
        self.status = "error"
        self._log(message)

    def _log(self, message: str) -> None:
        if self.debug:
            print(f"[LLMRunner {self.name}] {message}")
