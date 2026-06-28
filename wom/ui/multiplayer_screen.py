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
    ChatReceived,
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
from wom.ui.assets import ASSETS_DIR
from wom.ui.menu_screen import (
    INK,
    INK_DIM,
    INK_HOVER,
    MAP_SIZES,
    VICTORY_LABELS,
    VICTORY_MODES,
    _next,
)

CONNECT_TIMEOUT = 4.0

# Fondo de las pantallas secundarias: portada con un pergamino ancho a la
# derecha (data/assets/title-secondary.png). Zona útil del pergamino como
# fracciones del ancho/alto de la imagen (x0, y0, x1, y1). Si falta el asset, la
# pantalla cae al fondo plano de siempre.
TITLE_IMAGE_WIDE = "title-secondary.png"
SCROLL_AREA_WIDE = (0.41, 0.155, 0.90, 0.66)
MAX_CHAT_LINES = 60


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
        self.wants_internet = False  # el hub pide abrir el navegador de servidores
        self.net_start: NetGameStart | None = None

        self.title_font = pygame.font.SysFont(None, 48)
        self.font = pygame.font.SysFont(None, 30)
        self.small_font = pygame.font.SysFont(None, 22)
        self._buttons: dict[str, pygame.Rect] = {}
        self._fields: dict[str, pygame.Rect] = {}
        self.focused: str | None = None

        # Fondo "pergamino ancho" (cacheado al tamaño de la ventana).
        path = ASSETS_DIR / TITLE_IMAGE_WIDE
        self.background = pygame.image.load(str(path)) if path.exists() else None
        self._scaled_bg: pygame.Surface | None = None
        self._on_scroll = False  # True si se dibuja sobre el pergamino

        # Campos de texto.
        self.f_name = TextField(default_name)
        self.f_maxturns = TextField("50", numeric=True, max_len=4)
        self.f_turnsecs = TextField("0", numeric=True, max_len=4)
        self.f_hostport = TextField(str(DEFAULT_PORT), numeric=True, max_len=5)
        self.f_ip = TextField("127.0.0.1")
        self.f_connectport = TextField(str(DEFAULT_PORT), numeric=True, max_len=5)
        self.f_chat = TextField("", max_len=120)  # chat de la sala de espera
        self.chat_log: list[tuple[str, str]] = []  # (nombre, texto)

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
            "chat": self.f_chat,
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
        elif isinstance(event, ChatReceived):
            self._push_chat(event.name, event.text)
        elif isinstance(event, Started):
            self._begin_game()

    def _push_chat(self, name: str, text: str) -> None:
        self.chat_log.append((name, text))
        del self.chat_log[:-MAX_CHAT_LINES]

    def _send_chat(self) -> None:
        """Envía el texto del campo de chat de la sala y lo refleja localmente
        (el relay del host no devuelve el eco al emisor)."""
        text = self.f_chat.value.strip()
        self.f_chat.value = ""
        if not text or self.session is None:
            return
        self._push_chat(self.f_name.value or "yo", text)
        self.session.send_chat(text)

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
        if self.focused == "chat" and event.key in (pygame.K_RETURN, pygame.K_KP_ENTER):
            self._send_chat()  # Enter envía y conserva el foco para seguir charlando
            return
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
        elif hit == "to_internet":
            self.wants_internet = True
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
        window = surface.get_rect()
        area = self._paint_background(surface, window)
        head_color = INK if self._on_scroll else theme.TEXT
        title = self.title_font.render("Multijugador", True, head_color)
        surface.blit(title, title.get_rect(midtop=(area.centerx, area.y)))
        content = pygame.Rect(area.x, area.y + 52, area.width, area.height - 52)
        draw = {
            "hub": self._draw_hub,
            "create": self._draw_create,
            "connect": self._draw_connect,
            "waiting": self._draw_waiting,
            "started": self._draw_waiting,
        }[self.mode]
        draw(surface, content)
        if self.status:
            self._status_line(surface, content)

    def _paint_background(self, surface: pygame.Surface, window: pygame.Rect) -> pygame.Rect:
        """Pinta el fondo y devuelve el área útil del pergamino (ancho).
        Si falta el asset, cae al fondo plano centrado de siempre."""
        if self.background is not None:
            if self._scaled_bg is None or self._scaled_bg.get_size() != window.size:
                self._scaled_bg = pygame.transform.smoothscale(self.background, window.size)
            surface.blit(self._scaled_bg, (0, 0))
            x0, y0, x1, y1 = SCROLL_AREA_WIDE
            self._on_scroll = True
            return pygame.Rect(
                round(window.width * x0), round(window.height * y0),
                round(window.width * (x1 - x0)), round(window.height * (y1 - y0)),
            )
        surface.fill(theme.BACKGROUND)
        self._on_scroll = False
        area = pygame.Rect(0, 120, 640, window.height - 180)
        area.centerx = window.centerx
        return area

    def _col(self, area: pygame.Rect, width: int) -> pygame.Rect:
        """Columna centrada dentro del área ancha (para formularios)."""
        col = pygame.Rect(0, area.y, min(width, area.width), area.height)
        col.centerx = area.centerx
        return col

    def _status_line(self, surface: pygame.Surface, area: pygame.Rect) -> None:
        color = INK_DIM if self._on_scroll else theme.TEXT_DIM
        label = self.small_font.render(self.status, True, color)
        surface.blit(label, label.get_rect(midbottom=(area.centerx, area.bottom - 2)))

    def _draw_hub(self, surface: pygame.Surface, area: pygame.Rect) -> None:
        col = self._col(area, 460)
        cap_color = INK_DIM if self._on_scroll else theme.TEXT_DIM
        y = col.y + 8
        cap = self.small_font.render("Red local (LAN / IP directa)", True, cap_color)
        surface.blit(cap, cap.get_rect(midtop=(col.centerx, y)))
        y += 26
        y = self._button(surface, "to_create", "Crear partida", col, y, bordered=True)
        y = self._button(surface, "to_connect", "Conectarse", col, y, bordered=True)
        cap2 = self.small_font.render("Internet (servidor dedicado)", True, cap_color)
        surface.blit(cap2, cap2.get_rect(midtop=(col.centerx, y + 10)))
        y = self._button(surface, "to_internet", "Jugar por Internet", col, y + 36, bordered=True)
        self._button(surface, "back", "Volver (ESC)", col, y + 8)

    def _draw_create(self, surface: pygame.Surface, area: pygame.Rect) -> None:
        col = self._col(area, 480)
        y = col.y + 4
        y = self._field(surface, "name", "Tu nombre", self.f_name, col, y)
        width, height, _f, _t = MAP_SIZES[self.map_size]
        y = self._button(
            surface, "n_players", f"Jugadores:  {self.n_players}", col, y, option=True,
        )
        y = self._button(
            surface, "victory",
            f"Victoria:  {VICTORY_LABELS[self.victory_mode]}", col, y, option=True,
        )
        y = self._button(
            surface, "map_size", f"Mapa:  {self.map_size} ({width}x{height})",
            col, y, option=True,
        )
        y = self._field(surface, "maxturns", "Turnos máximos", self.f_maxturns, col, y)
        y = self._field(
            surface, "turnsecs", "Segundos por turno (0 = sin límite)",
            self.f_turnsecs, col, y,
        )
        y = self._field(surface, "hostport", "Puerto", self.f_hostport, col, y)
        y = self._button(surface, "host_start", "Esperar conexiones", col, y + 6, bordered=True)
        self._button(surface, "back", "Volver (ESC)", col, y)

    def _draw_connect(self, surface: pygame.Surface, area: pygame.Rect) -> None:
        col = self._col(area, 480)
        y = col.y + 14
        y = self._field(surface, "name", "Tu nombre", self.f_name, col, y)
        y = self._field(surface, "ip", "IP del host", self.f_ip, col, y)
        y = self._field(surface, "connectport", "Puerto", self.f_connectport, col, y)
        y = self._button(surface, "connect_start", "Conectar", col, y + 8, bordered=True)
        self._button(surface, "back", "Volver (ESC)", col, y)

    def _draw_waiting(self, surface: pygame.Surface, area: pygame.Rect) -> None:
        # El pergamino ancho permite dos columnas: la sala a la izquierda y el
        # chat a la derecha (solo con sesión viva y sobre el pergamino).
        chat_on = self._on_scroll and self.session is not None
        if chat_on:
            gap = 30
            left = pygame.Rect(area.x, area.y, int(area.width * 0.44), area.height)
            right = pygame.Rect(
                left.right + gap, area.y, area.right - (left.right + gap), area.height
            )
        else:
            left, right = self._col(area, 480), None
        self._draw_roster(surface, left)
        if right is not None:
            self._draw_chat(surface, right)

    def _draw_roster(self, surface: pygame.Surface, area: pygame.Rect) -> None:
        y = area.y + 4
        head = self.font.render(
            "Sala de espera", True, INK if self._on_scroll else theme.SELECTION
        )
        surface.blit(head, head.get_rect(midtop=(area.centerx, y)))
        y += 42
        row_color = INK if self._on_scroll else theme.TEXT
        for row in self._roster_rows():
            rendered = self.small_font.render(row, True, row_color)
            surface.blit(rendered, rendered.get_rect(midtop=(area.centerx, y)))
            y += 30
        y += 12
        in_lobby = (
            self.session is not None and self.session.state is SessionState.LOBBY
        )
        if self.mode == "waiting" and in_lobby:
            ready = self.session.local_ready
            y = self._button(
                surface, "ready", "Cancelar listo" if ready else "¡Listo!",
                area, y, bordered=True,
            )
        self._button(surface, "cancel", "Cancelar / Volver", area, y + 8)

    def _draw_chat(self, surface: pygame.Surface, area: pygame.Rect) -> None:
        head = self.small_font.render(
            "Chat de la sala", True, INK if self._on_scroll else theme.TEXT
        )
        surface.blit(head, (area.x, area.y))
        input_h, reserve = 50, 26
        box = pygame.Rect(
            area.x, area.y + 26, area.width, area.height - 26 - reserve - input_h - 4
        )
        if self._on_scroll:
            overlay = pygame.Surface(box.size, pygame.SRCALPHA)
            overlay.fill((255, 250, 235, 55))
            surface.blit(overlay, box.topleft)
            pygame.draw.rect(surface, INK, box, width=2, border_radius=6)
            line_color, dim_color = INK, INK_DIM
        else:
            pygame.draw.rect(surface, (24, 28, 34), box, border_radius=6)
            pygame.draw.rect(surface, (90, 96, 104), box, width=2, border_radius=6)
            line_color, dim_color = theme.TEXT, theme.TEXT_DIM
        lines = self._chat_display_lines(box.width - 16)
        if not lines:
            hint = self.small_font.render("Sin mensajes todavía…", True, dim_color)
            surface.blit(hint, (box.x + 8, box.y + 8))
        else:
            line_h = 22
            max_lines = max(1, (box.height - 12) // line_h)
            ty = box.y + 8
            for ln in lines[-max_lines:]:
                rendered = self.small_font.render(ln, True, line_color)
                surface.blit(rendered, (box.x + 8, ty))
                ty += line_h
        field_area = pygame.Rect(box.x - 20, box.bottom + 2, box.width + 40, input_h)
        self._field(
            surface, "chat", "Mensaje (Enter envía)", self.f_chat, field_area, field_area.y
        )

    def _chat_display_lines(self, max_width: int) -> list[str]:
        """Aplana el log de chat en líneas ajustadas al ancho del panel."""
        lines: list[str] = []
        for name, text in self.chat_log:
            cur = ""
            for word in f"{name}: {text}".split():
                probe = f"{cur} {word}".strip()
                if self.small_font.size(probe)[0] > max_width and cur:
                    lines.append(cur)
                    cur = word
                else:
                    cur = probe
            if cur:
                lines.append(cur)
        return lines

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
        self, surface, bid, label, area, y, option: bool = False, bordered: bool = False
    ) -> int:
        """Botón en estilo "tinta" sobre el pergamino: las opciones cíclicas son
        filas de texto con realce al pasar el mouse; las acciones (`bordered`)
        llevan marco de tinta para destacar. Degrada a botones rellenos sin el
        fondo de pergamino."""
        if option:
            rect = pygame.Rect(0, 0, area.width - 8, 36)
        else:
            rect = pygame.Rect(0, 0, min(360, area.width - 8), 46)
        rect.centerx = area.centerx
        rect.y = y
        over = rect.collidepoint(scale.mouse_pos())
        font = self.small_font if option else self.font
        if self._on_scroll:
            if bordered:
                if over:
                    highlight = pygame.Surface(rect.size, pygame.SRCALPHA)
                    highlight.fill((90, 55, 20, 35))
                    surface.blit(highlight, rect.topleft)
                pygame.draw.rect(
                    surface, INK_HOVER if over else INK, rect, width=2, border_radius=8
                )
            elif over:
                highlight = pygame.Surface(rect.size, pygame.SRCALPHA)
                highlight.fill((90, 55, 20, 45))
                surface.blit(highlight, rect.topleft)
            text = font.render(label, True, INK_HOVER if over else INK)
        else:
            if bordered:
                color = theme.BUTTON_BG_OVER if over else theme.BUTTON_BG
                pygame.draw.rect(surface, color, rect, width=2, border_radius=8)
            elif option:
                pygame.draw.rect(
                    surface, (70, 78, 86) if over else (50, 56, 62), rect, border_radius=8
                )
            else:
                bg = theme.BUTTON_BG_OVER if over else theme.BUTTON_BG
                pygame.draw.rect(surface, bg, rect, border_radius=8)
            text = font.render(label, True, theme.TEXT)
        surface.blit(text, text.get_rect(center=rect.center))
        self._buttons[bid] = rect
        return rect.bottom + (8 if self._on_scroll else 12)

    def _field(self, surface, fid, label, field: TextField, area, y) -> int:
        pad = 20
        cap_color = INK_DIM if self._on_scroll else theme.TEXT_DIM
        caption = self.small_font.render(label, True, cap_color)
        surface.blit(caption, (area.x + pad, y))
        rect = pygame.Rect(area.x + pad, y + 20, area.width - 2 * pad, 30)
        focused = self.focused == fid
        if self._on_scroll:
            overlay = pygame.Surface(rect.size, pygame.SRCALPHA)
            overlay.fill((255, 250, 235, 70))
            surface.blit(overlay, rect.topleft)
            pygame.draw.rect(
                surface, INK_HOVER if focused else INK, rect, width=2, border_radius=6
            )
            txt_color = INK
        else:
            pygame.draw.rect(surface, (30, 34, 40), rect, border_radius=6)
            pygame.draw.rect(
                surface, theme.TEXT if focused else (90, 96, 104), rect, width=2, border_radius=6
            )
            txt_color = theme.TEXT
        shown = field.value + ("_" if focused else "")
        text = self.font.render(shown, True, txt_color)
        surface.blit(text, (rect.x + 10, rect.y + 3))
        self._fields[fid] = rect
        return rect.bottom + 10
