"""Pantalla de Multijugador: hub, crear/conectar y sala de espera (lobby).

Es una pantalla de nivel superior del loop (como el menú o la partida). Maneja
toda la red de la fase de lobby para partidas de 2 a `MAX_PLAYERS` jugadores
(topología estrella, el host es la autoridad):

- **Hub**: crear partida o conectarse.
- **Crear** (host): nombre + reglas (jugadores, victoria, tamaño de mapa, turnos
  máximos, tiempo por turno, puerto) → "Esperar conexiones" abre el `Server`.
- **Conectar** (cliente): nombre + IP + puerto → "Conectar".
- **Sala de espera**: la lista de jugadores (host + rivales) con su estado de
  "listo", el botón "Listo" propio y "Cancelar". Cuando se conectaron todos y
  todos están listos, el host arranca y la pantalla pasa a "listo para jugar".

Conduce la `Session` (host o cliente) llamando a `update()` una vez por frame
desde el loop de la app y traduce sus eventos a estado de UI; el roster se lee
de la sesión cada frame. El arranque real de la partida (lockstep) lo hace
`NetGame` sobre el `NetGameStart` que se deja preparado acá.
"""

from __future__ import annotations

from dataclasses import dataclass

import pygame

from wom.core.game import Game, Player
from wom.core.mapgen import MapParams
from wom.core.victory import VictoryMode
from wom.core.worldmap import MAX_PLAYERS
from wom.net.protocol import GameSetup
from wom.net.rules import MatchRules
from wom.net.session import (
    ClientSession,
    Connected,
    Disconnected,
    GameReady,
    HostSession,
    Rejected,
    SessionState,
    Started,
)
from wom.net.transport import DEFAULT_PORT, Server, connect
from wom.ui import scale, theme
from wom.ui.menu_screen import MAP_SIZES, VICTORY_LABELS, VICTORY_MODES, _next

CONNECT_TIMEOUT = 4.0


@dataclass
class NetGameStart:
    """Todo lo que la app necesita para arrancar la partida en red."""

    role: str  # "host" | "client"
    session: HostSession | ClientSession
    game: Game
    human_id: int
    rules: MatchRules
    peer_name: str = ""  # lo deriva NetGame de los jugadores si va vacío


class TextField:
    """Campo de texto editable mínimo (foco + edición por teclado)."""

    def __init__(self, value: str = "", numeric: bool = False, max_len: int = 24):
        self.value = value
        self.numeric = numeric
        self.max_len = max_len

    def key(self, event: pygame.event.Event) -> None:
        if event.key == pygame.K_BACKSPACE:
            self.value = self.value[:-1]
        elif event.unicode and event.unicode.isprintable():
            ch = event.unicode
            if self.numeric and not ch.isdigit():
                return
            if len(self.value) < self.max_len:
                self.value += ch


class MultiplayerScreen:
    """Pantalla de configuración y lobby de partidas en red."""

    def __init__(self, default_name: str = "Jugador"):
        self.mode = "hub"
        self.wants_menu = False
        self.net_start: NetGameStart | None = None

        self.title_font = pygame.font.SysFont(None, 64)
        self.font = pygame.font.SysFont(None, 30)
        self.small_font = pygame.font.SysFont(None, 22)
        self._buttons: dict[str, pygame.Rect] = {}
        self._fields: dict[str, pygame.Rect] = {}
        self.focused: str | None = None

        # Campos de texto.
        self.f_name = TextField(default_name)
        self.f_maxturns = TextField("50", numeric=True, max_len=4)
        self.f_turnsecs = TextField("0", numeric=True, max_len=4)
        self.f_hostport = TextField(str(DEFAULT_PORT), numeric=True, max_len=5)
        self.f_ip = TextField("127.0.0.1")
        self.f_connectport = TextField(str(DEFAULT_PORT), numeric=True, max_len=5)

        # Reglas cíclicas (host).
        self.victory_mode = VictoryMode.TOTAL
        self.map_size = "medio"
        self.n_players = 2  # total de jugadores (2..MAX_PLAYERS)

        # Estado de red.
        self.role: str | None = None
        self.server: Server | None = None
        self.session: HostSession | ClientSession | None = None
        self.status = ""
        self._client_setup: GameSetup | None = None
        self._host_game: Game | None = None
        self._host_rules: MatchRules | None = None

    @property
    def capturing_text(self) -> bool:
        """True mientras un campo de texto tiene foco (suspende atajos globales)."""
        return self.focused is not None

    @property
    def _field_objs(self) -> dict[str, TextField]:
        return {
            "name": self.f_name,
            "maxturns": self.f_maxturns,
            "turnsecs": self.f_turnsecs,
            "hostport": self.f_hostport,
            "ip": self.f_ip,
            "connectport": self.f_connectport,
        }

    # --- red (llamado una vez por frame desde el loop) ---------------------

    def update(self) -> None:
        if self.mode != "waiting" or self.session is None:
            return
        # La HostSession saca las conexiones del propio Server (también durante
        # la partida, para las reconexiones); acá solo se la bombea.
        for event in self.session.update():
            self._on_net_event(event)
        if self.role == "host" and self.session.state is SessionState.CONNECTING:
            connected = len(self.session.roster()) - 1
            self.status = (
                f"Esperando jugadores ({connected}/{self.n_players - 1}) "
                f"en el puerto {self.server.port}…"
            )

    def _setup_provider(self, names: list[str]) -> GameSetup:
        """Construye el estado inicial cuando se conectaron todos los rivales.

        `names` viene ordenado por id (host primero). Todos los jugadores son
        humanos; el `human_id` de cada `GameSetup` lo pone la HostSession.
        """
        width, height, forts, towns = MAP_SIZES[self.map_size]
        forts = max(forts, self.n_players)  # un fuerte inicial por jugador
        players = [Player(i, names[i]) for i in range(self.n_players)]
        game = Game.new(
            MapParams(width, height, forts, towns, n_players=self.n_players),
            players,
            self.victory_mode,
        )
        self._host_rules = MatchRules(
            turn_seconds=int(self.f_turnsecs.value or 0),
            max_turns=int(self.f_maxturns.value or 50),
        )
        # El tope de turnos se hornea en el estado (viaja en el to_dict y lo
        # evalúa el core de forma idéntica en todos los clientes).
        game.turn_limit = self._host_rules.max_turns
        self._host_game = game
        return GameSetup(
            state=game.to_dict(),
            rules=self._host_rules.to_dict(),
            names=names,
            human_id=0,
        )

    def _on_net_event(self, event) -> None:
        if isinstance(event, Connected):
            if self.role == "host":
                self.status = f"{event.name} se unió a la sala."
            else:
                self.status = f"Conectado a {event.name}. Esperando a los demás…"
        elif isinstance(event, GameReady):
            self._client_setup = event.setup
            self.status = "Todos conectados. Marcá «Listo» para empezar."
        elif isinstance(event, Rejected):
            self.status = f"Conexión rechazada: {event.reason}"
            self._teardown()
        elif isinstance(event, Disconnected):
            self.status = f"Desconectado: {event.reason}"
            self._teardown()
        elif isinstance(event, Started):
            self._begin_game()

    def _begin_game(self) -> None:
        if self.role == "host":
            game = self._host_game
            rules = self._host_rules or MatchRules()
            human_id = 0
        else:
            setup = self._client_setup
            game = Game.from_dict(setup.state)
            rules = MatchRules.from_dict(setup.rules)
            human_id = setup.human_id
        self.net_start = NetGameStart(
            role=self.role,
            session=self.session,
            game=game,
            human_id=human_id,
            rules=rules,
        )
        self.status = "¡Listo! La partida en red comienza."
        self.mode = "started"

    def _teardown(self) -> None:
        """Cierra server/session y vuelve al estado de no conectado."""
        if self.session is not None:
            self.session.cancel("salió del lobby")
            self.session = None
        if self.server is not None:
            self.server.close()
            self.server = None
        self.role = None

    # --- input -------------------------------------------------------------

    def handle_event(self, event: pygame.event.Event) -> None:
        if event.type == pygame.KEYDOWN:
            if self.focused is not None:
                self._field_key(event)
                return
            if event.key == pygame.K_ESCAPE:
                self._go_back()
        elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            self._click(event.pos)

    def _field_key(self, event: pygame.event.Event) -> None:
        if event.key in (pygame.K_RETURN, pygame.K_KP_ENTER, pygame.K_ESCAPE, pygame.K_TAB):
            self.focused = None
            return
        self._field_objs[self.focused].key(event)

    def _click(self, point: tuple[int, int]) -> None:
        field_hit = next(
            (fid for fid, rect in self._fields.items() if rect.collidepoint(point)), None
        )
        if field_hit is not None:
            self.focused = field_hit
            return
        self.focused = None
        hit = next(
            (bid for bid, rect in self._buttons.items() if rect.collidepoint(point)), None
        )
        if hit is None:
            return
        self._activate(hit)

    def _activate(self, hit: str) -> None:
        if hit == "back":
            self._go_back()
        elif hit == "to_create":
            self.mode = "create"
        elif hit == "to_connect":
            self.mode = "connect"
        elif hit == "n_players":
            self.n_players = self.n_players % MAX_PLAYERS + 1
            if self.n_players < 2:
                self.n_players = 2
        elif hit == "victory":
            self.victory_mode = _next(VICTORY_MODES, self.victory_mode)
        elif hit == "map_size":
            self.map_size = _next(list(MAP_SIZES), self.map_size)
        elif hit == "host_start":
            self._start_hosting()
        elif hit == "connect_start":
            self._start_connecting()
        elif hit == "ready":
            self._toggle_ready()
        elif hit == "cancel":
            self._cancel_to_hub()

    def _go_back(self) -> None:
        if self.mode in ("create", "connect"):
            self.mode = "hub"
        elif self.mode == "waiting":
            self._cancel_to_hub()
        elif self.mode == "started":
            self._cancel_to_hub()
            self.wants_menu = True
        else:  # hub
            self.wants_menu = True

    def _start_hosting(self) -> None:
        try:
            port = int(self.f_hostport.value or DEFAULT_PORT)
            self.server = Server(host="0.0.0.0", port=port, max_clients=self.n_players - 1)
        except OSError as exc:
            self.status = f"No se pudo abrir el puerto: {exc}"
            return
        self.session = HostSession(
            self.n_players, self.f_name.value or "Host", self._setup_provider,
            server=self.server,
        )
        self.role = "host"
        self.mode = "waiting"
        self.status = f"Esperando jugadores en el puerto {self.server.port}…"

    def _start_connecting(self) -> None:
        ip = self.f_ip.value.strip() or "127.0.0.1"
        try:
            port = int(self.f_connectport.value or DEFAULT_PORT)
            conn = connect(ip, port, timeout=CONNECT_TIMEOUT)
        except OSError:
            self.status = f"No se pudo conectar a {ip}:{self.f_connectport.value}"
            return
        self.session = ClientSession(conn, self.f_name.value or "Jugador")
        self.role = "client"
        self.mode = "waiting"
        self.status = f"Conectando a {ip}…"

    def _toggle_ready(self) -> None:
        if self.session is None or self.session.state is not SessionState.LOBBY:
            return
        self.session.set_ready(not self.session.local_ready)

    def _cancel_to_hub(self) -> None:
        self._teardown()
        self.mode = "hub"
        self.status = ""

    # --- dibujo ------------------------------------------------------------

    def draw(self, surface: pygame.Surface) -> None:
        self._buttons.clear()
        self._fields.clear()
        surface.fill(theme.BACKGROUND)
        window = surface.get_rect()
        title = self.title_font.render("Multijugador", True, theme.TEXT)
        surface.blit(title, title.get_rect(center=(window.centerx, 70)))
        area = pygame.Rect(0, 140, 600, window.height - 200)
        area.centerx = window.centerx
        draw = {
            "hub": self._draw_hub,
            "create": self._draw_create,
            "connect": self._draw_connect,
            "waiting": self._draw_waiting,
            "started": self._draw_waiting,
        }[self.mode]
        draw(surface, area)
        if self.status:
            self._status_line(surface, window)

    def _status_line(self, surface: pygame.Surface, window: pygame.Rect) -> None:
        label = self.small_font.render(self.status, True, theme.TEXT_DIM)
        surface.blit(label, label.get_rect(center=(window.centerx, window.bottom - 40)))

    def _draw_hub(self, surface: pygame.Surface, area: pygame.Rect) -> None:
        y = area.y + 20
        y = self._button(surface, "to_create", "Crear partida", area, y)
        y = self._button(surface, "to_connect", "Conectarse", area, y)
        self._button(surface, "back", "Volver (ESC)", area, y + 10)

    def _draw_create(self, surface: pygame.Surface, area: pygame.Rect) -> None:
        y = area.y + 6
        y = self._field(surface, "name", "Tu nombre", self.f_name, area, y)
        width, height, _f, _t = MAP_SIZES[self.map_size]
        y = self._button(
            surface, "n_players", f"Jugadores:  {self.n_players}", area, y, option=True,
        )
        y = self._button(
            surface, "victory",
            f"Victoria:  {VICTORY_LABELS[self.victory_mode]}", area, y, option=True,
        )
        y = self._button(
            surface, "map_size", f"Mapa:  {self.map_size} ({width}x{height})",
            area, y, option=True,
        )
        y = self._field(surface, "maxturns", "Turnos máximos", self.f_maxturns, area, y)
        y = self._field(
            surface, "turnsecs", "Segundos por turno (0 = sin límite)",
            self.f_turnsecs, area, y,
        )
        y = self._field(surface, "hostport", "Puerto", self.f_hostport, area, y)
        y = self._button(surface, "host_start", "Esperar conexiones", area, y + 8)
        self._button(surface, "back", "Volver (ESC)", area, y, option=True)

    def _draw_connect(self, surface: pygame.Surface, area: pygame.Rect) -> None:
        y = area.y + 16
        y = self._field(surface, "name", "Tu nombre", self.f_name, area, y)
        y = self._field(surface, "ip", "IP del host", self.f_ip, area, y)
        y = self._field(surface, "connectport", "Puerto", self.f_connectport, area, y)
        y = self._button(surface, "connect_start", "Conectar", area, y + 8)
        self._button(surface, "back", "Volver (ESC)", area, y, option=True)

    def _draw_waiting(self, surface: pygame.Surface, area: pygame.Rect) -> None:
        y = area.y + 10
        label = self.font.render("Sala de espera", True, theme.SELECTION)
        surface.blit(label, label.get_rect(midtop=(area.centerx, y)))
        y += 44
        for row in self._roster_rows():
            rendered = self.font.render(row, True, theme.TEXT)
            surface.blit(rendered, rendered.get_rect(midtop=(area.centerx, y)))
            y += 38
        y += 10
        in_lobby = (
            self.session is not None and self.session.state is SessionState.LOBBY
        )
        if self.mode == "waiting" and in_lobby:
            ready = self.session.local_ready
            y = self._button(
                surface, "ready", "Cancelar listo" if ready else "¡Listo!", area, y
            )
        self._button(surface, "cancel", "Cancelar / Volver", area, y + 10)

    def _roster_rows(self) -> list[str]:
        """Filas de la sala: jugadores conectados (con su estado) + lugares
        libres que faltan ocupar."""
        rows: list[str] = []
        roster = self.session.roster() if self.session is not None else []
        for pid, name, ready in roster:
            tag = " (vos)" if self._is_local(pid) else ""
            state = "listo" if ready else "esperando"
            rows.append(f"J{pid + 1}: {name}{tag} — {state}")
        if self.role == "host":
            for _ in range(self.n_players - len(roster)):
                rows.append("· lugar libre…")
        elif not roster:
            rows.append("Conectando con el host…")
        return rows

    def _is_local(self, pid: int) -> bool:
        if self.role == "host":
            return pid == 0
        return self.session is not None and pid == self.session.human_id

    def _button(
        self, surface, bid, label, area, y, option: bool = False
    ) -> int:
        rect = pygame.Rect(0, 0, 460 if option else 380, 40 if option else 48)
        rect.centerx = area.centerx
        rect.y = y
        over = rect.collidepoint(scale.mouse_pos())
        if option:
            bg = (70, 78, 86) if over else (50, 56, 62)
        else:
            bg = theme.BUTTON_BG_OVER if over else theme.BUTTON_BG
        pygame.draw.rect(surface, bg, rect, border_radius=8)
        text = self.font.render(label, True, theme.TEXT)
        surface.blit(text, text.get_rect(center=rect.center))
        self._buttons[bid] = rect
        return rect.bottom + 12

    def _field(self, surface, fid, label, field: TextField, area, y) -> int:
        caption = self.small_font.render(label, True, theme.TEXT_DIM)
        surface.blit(caption, (area.x + 40, y))
        rect = pygame.Rect(area.x + 40, y + 20, area.width - 80, 34)
        focused = self.focused == fid
        pygame.draw.rect(surface, (30, 34, 40), rect, border_radius=6)
        pygame.draw.rect(
            surface, theme.TEXT if focused else (90, 96, 104), rect, width=2, border_radius=6
        )
        shown = field.value + ("_" if focused else "")
        text = self.font.render(shown, True, theme.TEXT)
        surface.blit(text, (rect.x + 10, rect.y + 4))
        self._fields[fid] = rect
        return rect.bottom + 14
