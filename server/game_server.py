"""Orquestador del servidor dedicado: junta transporte + lobby + N partidas.

`GameServer` es el corazón del proceso (fase S4 de `docs/server.md`): mantiene un
`transport.Server` escuchando, un `LobbyServer` (lobby + chat + catálogo) y un
`MatchRunner` por cada partida en curso. Un **único loop** (`tick`) drena los
eventos del lobby, los enruta a los runners (altas/bajas/reconexiones y mensajes
de partida) y avanza cada partida — sin hilo por partida (ver `docs/server.md`
§9). Cada partida está **aislada**: una excepción en un `MatchRunner` termina
solo esa partida, nunca el proceso.

Es stdlib-only y no importa pygame.
"""

from __future__ import annotations

import logging
import random
import signal
import time
from pathlib import Path

from wom.ai.ai_player import AIPlayer
from wom.net.lobby import (
    LobbyServer,
    MatchClosed,
    MatchMessage,
    MatchOpened,
    PlayerEntered,
    PlayerExited,
    SeatRejoined,
    SeatTaken,
    SeatVacated,
)
from wom.net.match import MatchRunner, Phase
from wom.net.protocol import Bye
from wom.net.transport import Server
from wom.persistence.scenario import list_maps, load_scenario

from server.config import ServerConfig

_AI_LEVEL = "medio"  # nivel de la IA que cubre ausentes / asientos vacíos
_TICK_SECONDS = 0.02  # ~50 Hz


class GameServer:
    """Servidor dedicado: lobby + varias partidas sobre un loop único."""

    def __init__(
        self,
        config: ServerConfig,
        *,
        rng: random.Random | None = None,
        log: logging.Logger | None = None,
    ) -> None:
        self.config = config
        self.log = log or logging.getLogger("wom.server")
        self._rng = rng or random.Random()
        self.transport = Server(
            host=config.host, port=config.port, max_clients=config.max_connections
        )
        self.lobby = LobbyServer(
            name=config.name,
            require_password=config.require_password,
            password=config.password,
            handshake_timeout=config.handshake_timeout,
            max_connections=config.max_connections,
            max_connections_per_ip=config.max_connections_per_ip,
            msg_rate=config.msg_rate,
            msg_burst=config.msg_burst,
            max_matches=config.max_matches,
            allow_random=config.allow_random,
            server=self.transport,
        )
        self.runners: dict[int, MatchRunner] = {}
        self._playing: set[int] = set()
        self._stop = False

    @property
    def port(self) -> int:
        return self.transport.port

    # --- loop principal ---------------------------------------------------

    def serve_forever(self) -> None:
        """Corre el loop hasta recibir SIGINT/SIGTERM (o `stop()`)."""
        self._install_signal_handlers()
        self.log.info("Servidor «%s» escuchando en %s:%s", self.config.name,
                      self.config.host, self.port)
        try:
            while not self._stop:
                self.tick()
                time.sleep(_TICK_SECONDS)
        finally:
            self.lobby.shutdown()
            self.transport.close()
            self.log.info("Servidor detenido.")

    def stop(self) -> None:
        self._stop = True

    def tick(self) -> None:
        """Una iteración del loop: drena el lobby, enruta y avanza las partidas."""
        for event in self.lobby.update():
            self._route(event)
        for match_id, runner in list(self.runners.items()):
            try:
                runner.update()
            except Exception:  # noqa: BLE001 — aísla el fallo de UNA partida
                self.log.exception("error en la partida %s; se cierra", match_id)
                self._fail_match(match_id, "error interno del servidor")
                continue
            if match_id not in self._playing and runner.phase is Phase.PLAYING:
                self._playing.add(match_id)
                self.lobby.mark_match_playing(match_id)
                self.log.info("partida %s en curso", match_id)
            if runner.finished:
                self._finish_match(match_id)

    # --- enrutado de eventos del lobby -----------------------------------

    def _route(self, event) -> None:
        try:
            self._dispatch(event)
        except Exception:  # noqa: BLE001
            mid = getattr(event, "match_id", None)
            self.log.exception("error enrutando %s", type(event).__name__)
            if mid is not None and mid in self.runners:
                self._fail_match(mid, "error interno del servidor")

    def _dispatch(self, event) -> None:
        if isinstance(event, PlayerEntered):
            self.log.info("entró %s (id %s)", event.name, event.lobby_id)
        elif isinstance(event, PlayerExited):
            self.log.info("salió %s (id %s)", event.name, event.lobby_id)
        elif isinstance(event, MatchOpened):
            self._open_match(event)
        elif isinstance(event, SeatTaken):
            runner = self.runners.get(event.match_id)
            if runner is not None:
                runner.player_joined(event.seat, event.lobby_id, self.lobby.name_of(event.lobby_id))
        elif isinstance(event, SeatVacated):
            runner = self.runners.get(event.match_id)
            if runner is not None:
                runner.player_left(event.seat, event.lobby_id)
        elif isinstance(event, SeatRejoined):
            runner = self.runners.get(event.match_id)
            if runner is not None:
                runner.player_rejoined(event.seat, event.lobby_id, self.lobby.name_of(event.lobby_id))
        elif isinstance(event, MatchClosed):
            self.runners.pop(event.match_id, None)
            self._playing.discard(event.match_id)
        elif isinstance(event, MatchMessage):
            runner = self.runners.get(event.match_id)
            if runner is not None:
                runner.handle(event.seat, event.lobby_id, event.message)

    def _open_match(self, event: MatchOpened) -> None:
        seed = self._rng.randrange(2**32)
        runner = MatchRunner(
            event.match_id,
            event.spec,
            self.lobby,  # el LobbyServer es el sink (send_to)
            seed=seed,
            ai_factory=self._ai_factory,
            scenario_loader=self._scenario_loader,
        )
        self.runners[event.match_id] = runner
        self.log.info("partida %s creada (%s, mapa %s)", event.match_id,
                      event.spec.name, event.spec.map_label())

    # --- cierre de partidas ----------------------------------------------

    def _finish_match(self, match_id: int) -> None:
        self.log.info("partida %s terminada", match_id)
        self.runners.pop(match_id, None)
        self._playing.discard(match_id)
        self.lobby.close_match(match_id)  # devuelve a los jugadores al lobby

    def _fail_match(self, match_id: int, reason: str) -> None:
        runner = self.runners.pop(match_id, None)
        self._playing.discard(match_id)
        if runner is not None:
            for lobby_id in runner.seats.values():
                self.lobby.send_to(lobby_id, Bye(reason=reason))
        self.lobby.close_match(match_id)

    # --- fábricas ---------------------------------------------------------

    def _ai_factory(self, seat: int) -> AIPlayer:
        return AIPlayer(seat, level=_AI_LEVEL)

    def _scenario_loader(self, ref: str):
        """Resuelve un `.wom` por nombre dentro de la carpeta de escenarios."""
        dirs = [Path(self.config.scenarios_dir)]
        for path in list_maps(dirs):
            if path.name == ref or path.stem == ref:
                return load_scenario(path)
        raise FileNotFoundError(f"escenario no encontrado: {ref}")

    # --- señales ----------------------------------------------------------

    def _install_signal_handlers(self) -> None:
        def _handler(_signum, _frame):
            self.log.info("señal de cierre recibida")
            self._stop = True

        for sig in (signal.SIGINT, getattr(signal, "SIGTERM", None)):
            if sig is None:
                continue
            try:
                signal.signal(sig, _handler)
            except (ValueError, OSError):
                # No estamos en el hilo principal (p. ej. en un test): se omite.
                pass
