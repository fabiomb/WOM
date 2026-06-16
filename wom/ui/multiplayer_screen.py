"""Pantalla de Multijugador: hub, crear/conectar y sala de espera (lobby).

Es una pantalla de nivel superior del loop (como el menú o la partida). Maneja
toda la red de la fase de lobby:

- **Hub**: crear partida o conectarse.
- **Crear** (host): nombre + reglas (victoria, tamaño de mapa, turnos máximos,
  tiempo por turno, puerto) → "Esperar conexiones" abre el `Server`.
- **Conectar** (cliente): nombre + IP + puerto → "Conectar".
- **Sala de espera**: estado de la conexión, aviso cuando entra el rival, botón
  "Listo" de cada uno y "Cancelar". Cuando ambos están listos el host arranca
  y la pantalla pasa a "listo para jugar".

Conduce la `Session` (host o cliente) llamando a `update()` una vez por frame
desde el loop de la app y traduce sus eventos a estado de UI. El arranque real
de la partida en red (intercambio de órdenes, `run_turn` sincronizado) es de la
fase MP4: acá se deja preparado `net_start` con todo lo necesario.
"""

from __future__ import annotations

from dataclasses import dataclass

import pygame

from wom.core.game import Game, Player
from wom.core.mapgen import MapParams
from wom.core.victory import VictoryMode
from wom.net.protocol import GameSetup
from wom.net.rules import MatchRules
from wom.net.session import (
    ClientSession,
    Connected,
    Disconnected,
    GameReady,
    HostSession,
    ReadyChanged,
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
    """Todo lo que MP4 necesita para arrancar la partida en red."""

    role: str  # "host" | "client"
    session: HostSession | ClientSession
    game: Game
    human_id: int
    rules: MatchRules
    peer_name: str


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

        # Estado de red.
        self.role: str | None = None
        self.server: Server | None = None
        self.session: HostSession | ClientSession | None = None
        self.status = ""
        self.local_ready = False
        self.peer_ready = False
        self.peer_name: str | None = None
        self._client_setup: GameSetup | None = None
        self._host_game: Game | None = None
        self._host_rules: MatchRules | None = None

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
        if self.mode != "waiting":
            return
        if self.role == "host" and self.session is None and self.server is not None:
            conn = self.server.poll_connection()
            if conn is not None:
                self.session = HostSession(
                    conn, self.f_name.value or "Host", self._setup_provider
                )
                self.status = "Jugador conectado, validando…"
        if self.session is not None:
            for event in self.session.update():
                self._on_net_event(event)

    def _setup_provider(self, client_name: str) -> GameSetup:
        """Construye el estado inicial de la partida al conectarse el cliente."""
        host_name = self.f_name.value or "Host"
        width, height, forts, towns = MAP_SIZES[self.map_size]
        players = [Player(0, host_name), Player(1, client_name)]
        game = Game.new(MapParams(width, height, forts, towns), players, self.victory_mode)
        self._host_game = game
        self._host_rules = MatchRules(
            turn_seconds=int(self.f_turnsecs.value or 0),
            max_turns=int(self.f_maxturns.value or 50),
        )
        return GameSetup(
            state=game.to_dict(),
            rules=self._host_rules.to_dict(),
            names=[host_name, client_name],
        )

    def _on_net_event(self, event) -> None:
        if isinstance(event, Connected):
            self.peer_name = event.peer_name
            self.status = f"{event.peer_name} conectado. Marcá «Listo» para empezar."
        elif isinstance(event, GameReady):
            self._client_setup = event.setup
        elif isinstance(event, ReadyChanged):
            self.peer_ready = event.ready
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
            human_id = 1
        self.net_start = NetGameStart(
            role=self.role,
            session=self.session,
            game=game,
            human_id=human_id,
            rules=rules,
            peer_name=self.peer_name or "",
        )
        self.status = "¡Listo! La partida en red comienza (integración: MP4)."
        self.mode = "started"

    def _teardown(self) -> None:
        """Cierra server/session y vuelve al estado de no conectado."""
        if self.session is not None:
            self.session.connection.close()
            self.session = None
        if self.server is not None:
            self.server.close()
            self.server = None
        self.role = None
        self.local_ready = False
        self.peer_ready = False

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
            self.server = Server(host="0.0.0.0", port=port)
        except OSError as exc:
            self.status = f"No se pudo abrir el puerto: {exc}"
            return
        self.role = "host"
        self.mode = "waiting"
        self.local_ready = self.peer_ready = False
        self.status = f"Esperando jugador en el puerto {self.server.port}…"

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
        self.local_ready = self.peer_ready = False
        self.status = f"Conectando a {ip}…"

    def _toggle_ready(self) -> None:
        if self.session is None or self.session.state is not SessionState.LOBBY:
            return
        self.local_ready = not self.local_ready
        self.session.set_ready(self.local_ready)

    def _cancel_to_hub(self) -> None:
        if self.session is not None:
            self.session.cancel("salió del lobby")
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
        y = area.y + 20
        rows = [
            f"Vos: {self.f_name.value or '—'}  {'✔ listo' if self.local_ready else ''}",
            f"Rival: {self.peer_name or 'esperando…'}"
            f"  {'✔ listo' if self.peer_ready else ''}",
        ]
        for text in rows:
            label = self.font.render(text, True, theme.TEXT)
            surface.blit(label, label.get_rect(midtop=(area.centerx, y)))
            y += 44
        y += 10
        connected = (
            self.session is not None and self.session.state is SessionState.LOBBY
        )
        if self.mode == "waiting" and connected:
            ready_label = "Cancelar listo" if self.local_ready else "¡Listo!"
            y = self._button(surface, "ready", ready_label, area, y)
        if self.mode == "started":
            y += 10
        self._button(surface, "cancel", "Cancelar / Volver", area, y + 10)

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
