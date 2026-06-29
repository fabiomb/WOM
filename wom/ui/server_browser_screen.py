"""Navegador de servidores (Multijugador → Internet), fase S6.

Pantalla de nivel superior del loop. Administra la **lista de servidores
guardados** (agregar/editar/borrar, persistida en `settings.json`), conecta a uno
con una `ServerSession` y muestra el **lobby** (roster + estado) y los avisos de
error del servidor. La vista completa del lobby (chat, catálogo de partidas,
crear/unirse y arranque de la partida) llega en S7.

Modos: ``browser`` (lista + nombre del jugador) → ``form`` (alta/edición) ·
``connecting`` (handshake) · ``lobby`` (conectado). Sigue el mismo patrón que
`MultiplayerScreen` (campos de texto con foco, rects de botones por frame).
"""

from __future__ import annotations

import pygame

from wom.core.game import Game
from wom.core.mapgen import MAP_SIZES
from wom.core.worldmap import MAX_PLAYERS
from wom.net.rules import MatchRules, TACTICAL_MODES, TACTICAL_OFF
from wom.ui.multiplayer_screen import TACTICAL_LABELS
from wom.net.server_session import (
    ChatReceived,
    Connected,
    Disconnected,
    ErrorReceived,
    GameReady,
    LobbyChatReceived,
    LobbyUpdated,
    MatchJoinedEvent,
    Rejected,
    ServerSession,
    Started,
)
from wom.net.transport import DEFAULT_PORT, connect
from wom.persistence.settings import (
    add_server,
    load_settings,
    remove_server,
    save_settings,
    update_server,
)
from wom.ui import scale, theme
from wom.ui.assets import ASSETS_DIR
from wom.ui.menu_screen import INK, INK_DIM, INK_HOVER
from wom.ui.multiplayer_screen import (
    SCROLL_AREA_WIDE,
    TITLE_IMAGE_WIDE,
    NetGameStart,
    TextField,
)

CONNECT_TIMEOUT = 4.0
CHAT_LOG_MAX = 8


class ServerBrowserScreen:
    """Lista de servidores + conexión + lobby (multijugador por Internet)."""

    def __init__(self, settings_path=None, session: ServerSession | None = None):
        # `session` viva ⇒ se retoma el lobby tras una partida (no se reconecta).
        self.mode = "lobby" if session is not None else "browser"
        self.wants_menu = False
        self._settings_path = settings_path

        self.title_font = pygame.font.SysFont(None, 48)
        self.font = pygame.font.SysFont(None, 30)
        self.small_font = pygame.font.SysFont(None, 22)
        self._buttons: dict[str, pygame.Rect] = {}
        self._fields: dict[str, pygame.Rect] = {}
        self._rows: dict[int, pygame.Rect] = {}
        self.focused: str | None = None

        # Fondo "pergamino ancho" (mismo que la pantalla de Multijugador).
        path = ASSETS_DIR / TITLE_IMAGE_WIDE
        self.background = pygame.image.load(str(path)) if path.exists() else None
        self._scaled_bg: pygame.Surface | None = None
        self._on_scroll = False

        self._settings = load_settings(self._settings_path)
        self.servers: list[dict] = [dict(s) for s in self._settings.servers]
        self.selected: int | None = 0 if self.servers else None

        self.f_player = TextField(self._settings.player_name or "Jugador")
        self.f_sname = TextField("", max_len=24)
        self.f_shost = TextField("", max_len=64)
        self.f_sport = TextField(str(DEFAULT_PORT), numeric=True, max_len=5)
        self._edit_index: int | None = None  # None = alta nueva

        # Lobby: chat global, catálogo de partidas y formulario de crear.
        self.f_chat = TextField("", max_len=120)
        self.f_mname = TextField("Mi partida", max_len=24)
        self.f_mturnsecs = TextField("0", numeric=True, max_len=4)
        self.f_mmaxturns = TextField("50", numeric=True, max_len=4)
        self.create_players = 2
        self.create_size = "medio"  # tamaño de mapa de la partida a crear
        self.create_tactical = TACTICAL_OFF  # zoom de batalla en red
        self.chat_log: list[tuple[str, str]] = []      # chat global del lobby
        self.room_chat_log: list[tuple[str, str]] = []  # chat de la sala (partida)
        self.selected_match: int | None = None
        self._match_rows: dict[int, pygame.Rect] = {}

        # Sala (partida en la que estoy) y arranque.
        self.match_id: int | None = None
        self.seat: int | None = None
        self.local_ready = False
        self._setup = None  # GameSetup recibido
        self.net_start: NetGameStart | None = None

        self.session: ServerSession | None = session
        self.status = (
            f"De vuelta en el lobby de {session.server_name}." if session is not None else ""
        )
        self.status_error = False  # resalta el status (rojo) cuando es un rechazo/error

    @property
    def capturing_text(self) -> bool:
        return self.focused is not None

    @property
    def _field_objs(self) -> dict[str, TextField]:
        return {
            "player": self.f_player,
            "sname": self.f_sname,
            "shost": self.f_shost,
            "sport": self.f_sport,
            "chat": self.f_chat,
            "mname": self.f_mname,
            "mturnsecs": self.f_mturnsecs,
            "mmaxturns": self.f_mmaxturns,
        }

    # --- red (una vez por frame desde el loop) ---------------------------

    def update(self) -> None:
        if self.session is None:
            return
        for event in self.session.update():
            self._on_net_event(event)

    def _on_net_event(self, event) -> None:
        if isinstance(event, Connected):
            self.mode = "lobby"
            self._set_status(f"Conectado a {event.server_name}.")
        elif isinstance(event, Rejected):
            self._set_status(f"No se pudo entrar: {event.reason}", error=True)
            self._disconnect(to="browser")
        elif isinstance(event, Disconnected):
            self._set_status(f"Desconectado: {event.reason}", error=True)
            self._disconnect(to="browser")
        elif isinstance(event, ErrorReceived):
            self._set_status(event.message, error=True)
        elif isinstance(event, ChatReceived):  # chat DENTRO de la sala/partida
            self.room_chat_log.append((event.name, event.text))
            del self.room_chat_log[:-CHAT_LOG_MAX]
        elif isinstance(event, LobbyChatReceived):
            self.chat_log.append((event.name, event.text))
            del self.chat_log[:-CHAT_LOG_MAX]
        elif isinstance(event, LobbyUpdated):
            # El match seleccionado puede haber desaparecido del catálogo.
            ids = {row[0] for row in event.matches}
            if self.selected_match not in ids:
                self.selected_match = None
        elif isinstance(event, MatchJoinedEvent):
            self.match_id = event.match_id
            self.seat = event.seat
            self.local_ready = False
            self.room_chat_log = []
            self.mode = "room"
            self._set_status("En la sala. Chateá y marcá «Listo» cuando estés.")
        elif isinstance(event, GameReady):
            self._setup = event.setup
            self.seat = event.setup.human_id
        elif isinstance(event, Started):
            self._begin_game()

    def _set_status(self, text: str, error: bool = False) -> None:
        self.status = text
        self.status_error = error

    def _begin_game(self) -> None:
        if self._setup is None:
            return
        game = Game.from_dict(self._setup.state)
        self.net_start = NetGameStart(
            role="client",
            session=self.session,
            game=game,
            human_id=self._setup.human_id,
            rules=MatchRules.from_dict(self._setup.rules),
        )
        self.status = "¡Listo! La partida comienza."

    # --- input -----------------------------------------------------------

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
        if event.key in (pygame.K_RETURN, pygame.K_KP_ENTER):
            if self.focused == "chat":  # Enter en el chat: envía y sigue escribiendo
                self._send_chat()
                return
            self.focused = None
            return
        if event.key in (pygame.K_ESCAPE, pygame.K_TAB):
            self.focused = None
            return
        self._field_objs[self.focused].key(event)

    def _send_chat(self) -> None:
        text = self.f_chat.value.strip()
        if not text or self.session is None:
            return
        if self.mode == "room":
            # El servidor relaya el chat de la sala SIN eco al emisor, así que
            # sumamos el mensaje propio al log local para verlo (como el chat
            # en partida). El chat global del lobby sí vuelve del servidor.
            self.session.send_chat(text)
            self.room_chat_log.append((self.session.name, text))
            del self.room_chat_log[:-CHAT_LOG_MAX]
        else:
            self.session.send_lobby_chat(text)  # chat global del lobby (vuelve a todos)
        self.f_chat.value = ""

    def _click(self, point: tuple[int, int]) -> None:
        field_hit = next((f for f, r in self._fields.items() if r.collidepoint(point)), None)
        if field_hit is not None:
            self.focused = field_hit
            return
        self.focused = None
        match_hit = next((mid for mid, r in self._match_rows.items() if r.collidepoint(point)), None)
        if match_hit is not None:
            self.selected_match = match_hit
            return
        row_hit = next((i for i, r in self._rows.items() if r.collidepoint(point)), None)
        if row_hit is not None:
            self.selected = row_hit
            return
        hit = next((b for b, r in self._buttons.items() if r.collidepoint(point)), None)
        if hit is not None:
            self._activate(hit)

    def _activate(self, hit: str) -> None:
        if hit == "back":
            self._go_back()
        elif hit == "add":
            self._open_form(None)
        elif hit == "edit" and self.selected is not None:
            self._open_form(self.selected)
        elif hit == "delete" and self.selected is not None:
            self._delete_selected()
        elif hit == "connect":
            self._connect()
        elif hit == "save_form":
            self._save_form()
        elif hit == "cancel_form":
            self.mode = "browser"
        elif hit == "disconnect":
            self._disconnect(to="browser")
        # --- lobby / sala ---
        elif hit == "open_create":
            self.mode = "createform"
        elif hit == "cmplayers":
            self.create_players = self.create_players % MAX_PLAYERS + 1
            if self.create_players < 2:
                self.create_players = 2
        elif hit == "cmsize":
            sizes = list(MAP_SIZES)
            self.create_size = sizes[(sizes.index(self.create_size) + 1) % len(sizes)]
        elif hit == "cmtactical":
            modes = list(TACTICAL_MODES)
            self.create_tactical = modes[(modes.index(self.create_tactical) + 1) % len(modes)]
        elif hit == "create_do":
            self._create_match()
        elif hit == "cancel_create":
            self.mode = "lobby"
        elif hit == "join" and self.selected_match is not None:
            self.session.join_match(self.selected_match)
        elif hit == "send_chat":
            self._send_chat()
        elif hit == "ready":
            self._toggle_ready()
        elif hit == "leaveroom":
            self._leave_room()

    def _create_match(self) -> None:
        if self.session is None:
            return
        rules = {
            "turn_seconds": int(self.f_mturnsecs.value or 0),
            "max_turns": int(self.f_mmaxturns.value or 50),
            "map_size": self.create_size,
            "tactical_mode": self.create_tactical,
        }
        self.session.create_match(
            name=self.f_mname.value.strip() or "Partida",
            max_players=self.create_players,
            map_source="random",
            rules=rules,
        )
        self.mode = "lobby"  # el MatchJoinedEvent llevará a la sala

    def _toggle_ready(self) -> None:
        if self.session is None:
            return
        self.local_ready = not self.local_ready
        self.session.set_ready(self.local_ready)

    def _leave_room(self) -> None:
        if self.session is not None:
            self.session.leave_match()
        self.match_id = None
        self.seat = None
        self.local_ready = False
        self._setup = None
        self.mode = "lobby"

    def _go_back(self) -> None:
        if self.mode == "form":
            self.mode = "browser"
        elif self.mode == "createform":
            self.mode = "lobby"
        elif self.mode == "room":
            self._leave_room()
        elif self.mode in ("lobby", "connecting"):
            self._disconnect(to="browser")
        else:  # browser
            self._persist()
            self.wants_menu = True

    # --- alta/edición de servidores --------------------------------------

    def _open_form(self, index: int | None) -> None:
        self._edit_index = index
        if index is not None and 0 <= index < len(self.servers):
            s = self.servers[index]
            self.f_sname.value = s.get("name", "")
            self.f_shost.value = s.get("host", "")
            self.f_sport.value = str(s.get("port", DEFAULT_PORT))
        else:
            self.f_sname.value = ""
            self.f_shost.value = ""
            self.f_sport.value = str(DEFAULT_PORT)
        self.mode = "form"

    def _save_form(self) -> None:
        host = self.f_shost.value.strip()
        if not host:
            self._set_status("Falta la dirección del servidor.", error=True)
            return
        name = self.f_sname.value.strip() or host
        port = int(self.f_sport.value or DEFAULT_PORT)
        if self._edit_index is None:
            self.servers = add_server(self.servers, name, host, port)
            self.selected = len(self.servers) - 1
        else:
            self.servers = update_server(self.servers, self._edit_index, name, host, port)
            self.selected = self._edit_index
        self._persist()
        self.mode = "browser"

    def _delete_selected(self) -> None:
        self.servers = remove_server(self.servers, self.selected)
        self._persist()
        if not self.servers:
            self.selected = None
        else:
            self.selected = min(self.selected, len(self.servers) - 1)

    def _persist(self) -> None:
        self._settings.servers = [dict(s) for s in self.servers]
        self._settings.player_name = self.f_player.value.strip() or "Jugador"
        save_settings(self._settings, self._settings_path)

    # --- conexión --------------------------------------------------------

    def _connect(self) -> None:
        if self.selected is None or not self.servers:
            self._set_status("Elegí un servidor de la lista.", error=True)
            return
        self._persist()
        s = self.servers[self.selected]
        host = s.get("host", "")
        port = int(s.get("port", DEFAULT_PORT))
        try:
            conn = connect(host, port, timeout=CONNECT_TIMEOUT)
        except OSError:
            self._set_status(f"No se pudo conectar a {host}:{port}", error=True)
            return
        self.session = ServerSession(conn, self.f_player.value.strip() or "Jugador")
        self.mode = "connecting"
        self._set_status(f"Conectando a {host}…")

    def _disconnect(self, to: str = "browser") -> None:
        if self.session is not None:
            self.session.cancel("salió del lobby")
            self.session = None
        self.mode = to

    # --- dibujo ----------------------------------------------------------

    def draw(self, surface: pygame.Surface) -> None:
        self._buttons.clear()
        self._fields.clear()
        self._rows.clear()
        self._match_rows.clear()
        window = surface.get_rect()
        area = self._paint_background(surface, window)
        title = self.title_font.render(
            "Jugar por Internet", True, INK if self._on_scroll else theme.TEXT
        )
        surface.blit(title, title.get_rect(midtop=(area.centerx, area.y)))
        content = pygame.Rect(area.x, area.y + 50, area.width, area.height - 50)
        {
            "browser": self._draw_browser,
            "form": self._draw_form,
            "connecting": self._draw_lobby,
            "lobby": self._draw_lobby,
            "createform": self._draw_createform,
            "room": self._draw_room,
        }[self.mode](surface, content)
        if self.status:
            self._draw_status(surface, content)

    def _paint_background(self, surface: pygame.Surface, window: pygame.Rect) -> pygame.Rect:
        """Pinta el fondo y devuelve el área útil del pergamino ancho. Cae al
        fondo plano de siempre si falta el asset."""
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
        area = pygame.Rect(0, 110, 760, window.height - 160)
        area.centerx = window.centerx
        return area

    def _col(self, area: pygame.Rect, width: int) -> pygame.Rect:
        col = pygame.Rect(0, area.y, min(width, area.width), area.height)
        col.centerx = area.centerx
        return col

    def _draw_status(self, surface: pygame.Surface, area: pygame.Rect) -> None:
        """Línea de estado. Si es un error/rechazo, va en una barra resaltada
        (rojo) para que el motivo no pase desapercibido."""
        if self.status_error:
            label = self.small_font.render(self.status, True, (255, 235, 230))
            box = label.get_rect(midbottom=(area.centerx, area.bottom))
            box.inflate_ip(36, 14)
            pygame.draw.rect(surface, (150, 50, 45), box, border_radius=8)
            pygame.draw.rect(surface, (210, 90, 80), box, width=2, border_radius=8)
            surface.blit(label, label.get_rect(center=box.center))
        else:
            color = INK_DIM if self._on_scroll else theme.TEXT_DIM
            label = self.small_font.render(self.status, True, color)
            surface.blit(label, label.get_rect(midbottom=(area.centerx, area.bottom - 2)))

    def _draw_browser(self, surface: pygame.Surface, area: pygame.Rect) -> None:
        col = self._col(area, 540)
        y = self._field(surface, "player", "Tu nombre", self.f_player, col, col.y + 4)
        y += 4
        cap_color = INK_DIM if self._on_scroll else theme.TEXT_DIM
        caption = self.small_font.render("Servidores guardados", True, cap_color)
        surface.blit(caption, (col.x + 20, y))
        y += 24
        if not self.servers:
            empty = self.small_font.render("(ninguno — agregá uno)", True, cap_color)
            surface.blit(empty, (col.x + 30, y))
            y += 28
        for i, s in enumerate(self.servers):
            label = f"{s.get('name', '')}   {s.get('host', '')}:{s.get('port', '')}"
            y = self._list_row(surface, col, y, label, i == self.selected, i, self._rows)
        y += 6
        y = self._button(surface, "connect", "Conectar", col, y, bordered=True)
        y = self._button(surface, "add", "Agregar", col, y, option=True)
        if self.servers:
            y = self._button(surface, "edit", "Editar", col, y, option=True)
            y = self._button(surface, "delete", "Borrar", col, y, option=True)
        self._button(surface, "back", "Volver (ESC)", col, y + 4, option=True)

    def _draw_form(self, surface: pygame.Surface, area: pygame.Rect) -> None:
        col = self._col(area, 520)
        y = col.y + 6
        head = "Editar servidor" if self._edit_index is not None else "Nuevo servidor"
        label = self.font.render(head, True, INK if self._on_scroll else theme.SELECTION)
        surface.blit(label, label.get_rect(midtop=(col.centerx, y)))
        y += 46
        y = self._field(surface, "sname", "Nombre (opcional)", self.f_sname, col, y)
        y = self._field(surface, "shost", "Dirección (IP o dominio)", self.f_shost, col, y)
        y = self._field(surface, "sport", "Puerto", self.f_sport, col, y)
        y = self._button(surface, "save_form", "Guardar", col, y + 6, bordered=True)
        self._button(surface, "cancel_form", "Cancelar", col, y, option=True)

    def _draw_lobby(self, surface: pygame.Surface, area: pygame.Rect) -> None:
        name = self.session.server_name if self.session is not None else ""
        head = self.font.render(
            f"Lobby — {name}" if name else "Conectando…",
            True, INK if self._on_scroll else theme.SELECTION,
        )
        surface.blit(head, head.get_rect(midtop=(area.centerx, area.y)))
        body = pygame.Rect(area.x, area.y + 40, area.width, area.height - 40)
        left, right = self._split_for_chat(body)
        # --- izquierda: jugadores + catálogo + acciones ---
        cap_color = INK_DIM if self._on_scroll else theme.TEXT_DIM
        row_color = INK if self._on_scroll else theme.TEXT
        y = left.y + 2
        players = self.session.players if self.session is not None else []
        nicks = ", ".join(p[1] for p in players) or "(esperando…)"
        surface.blit(self.small_font.render(f"Conectados: {nicks}", True, cap_color), (left.x + 10, y))
        y += 28
        surface.blit(self.small_font.render("Partidas disponibles", True, cap_color), (left.x + 10, y))
        y += 24
        matches = self.session.matches if self.session is not None else []
        if not matches:
            surface.blit(self.small_font.render("(ninguna — creá una)", True, cap_color), (left.x + 20, y))
            y += 28
        for row in matches:
            mid, mname, maxp, occ, estado, mapa = row[0], row[1], row[2], row[3], row[4], row[5]
            label = f"{mname}   {occ}/{maxp}   {estado}   {mapa}"
            y = self._list_row(surface, left, y, label, mid == self.selected_match, mid, self._match_rows)
        y += 4
        y = self._button(surface, "open_create", "Crear partida", left, y, option=True)
        if self.selected_match is not None:
            y = self._button(surface, "join", "Unirse a la seleccionada", left, y, option=True)
        self._button(surface, "disconnect", "Desconectar", left, y, option=True)
        # --- derecha: chat global ---
        if right is not None:
            self._draw_chat_panel(surface, right, "Chat", self.chat_log)

    def _draw_createform(self, surface: pygame.Surface, area: pygame.Rect) -> None:
        col = self._col(area, 520)
        y = col.y + 6
        head = self.font.render("Crear partida", True, INK if self._on_scroll else theme.SELECTION)
        surface.blit(head, head.get_rect(midtop=(col.centerx, y)))
        y += 46
        y = self._field(surface, "mname", "Nombre de la partida", self.f_mname, col, y)
        y = self._button(surface, "cmplayers", f"Jugadores:  {self.create_players}", col, y, option=True)
        w, h, _f, _t = MAP_SIZES[self.create_size]
        y = self._button(surface, "cmsize", f"Mapa:  {self.create_size} ({w}x{h})", col, y, option=True)
        y = self._button(
            surface, "cmtactical",
            f"Zoom de batalla:  {TACTICAL_LABELS[self.create_tactical]}", col, y, option=True,
        )
        y = self._field(surface, "mmaxturns", "Turnos máximos", self.f_mmaxturns, col, y)
        y = self._field(surface, "mturnsecs", "Segundos por turno (0 = sin límite)", self.f_mturnsecs, col, y)
        y = self._button(surface, "create_do", "Crear", col, y + 6, bordered=True)
        self._button(surface, "cancel_create", "Cancelar", col, y, option=True)

    def _draw_room(self, surface: pygame.Surface, area: pygame.Rect) -> None:
        head = self.font.render(
            "Sala de espera", True, INK if self._on_scroll else theme.SELECTION
        )
        surface.blit(head, head.get_rect(midtop=(area.centerx, area.y)))
        body = pygame.Rect(area.x, area.y + 40, area.width, area.height - 40)
        left, right = self._split_for_chat(body)
        cap_color = INK_DIM if self._on_scroll else theme.TEXT_DIM
        text_color = INK if self._on_scroll else theme.TEXT
        y = left.y + 2
        info = next(
            (r for r in (self.session.matches if self.session else []) if r[0] == self.match_id),
            None,
        )
        if info is not None:
            line = f"{info[1]} — {info[3]}/{info[2]} jugadores — {info[4]}"
            surface.blit(self.small_font.render(line, True, text_color), (left.x + 10, y))
            y += 30
        seat_txt = f"Tu asiento: J{(self.seat or 0) + 1}"
        surface.blit(self.small_font.render(seat_txt, True, cap_color), (left.x + 10, y))
        y += 28
        wait = (
            "Esperando a que se sumen y todos estén listos…"
            if not self.local_ready else "Listo. Esperando al resto…"
        )
        surface.blit(self.small_font.render(wait, True, cap_color), (left.x + 10, y))
        y += 34
        y = self._button(
            surface, "ready", "Cancelar listo" if self.local_ready else "¡Listo!",
            left, y, bordered=True,
        )
        self._button(surface, "leaveroom", "Salir de la sala", left, y, option=True)
        if right is not None:
            self._draw_chat_panel(surface, right, "Chat de la sala", self.room_chat_log)

    # --- helpers de dibujo ----------------------------------------------

    def _split_for_chat(self, body: pygame.Rect) -> tuple[pygame.Rect, pygame.Rect | None]:
        """Parte el cuerpo en columna izquierda (contenido) + derecha (chat).
        Sin sesión viva no hay chat: una sola columna centrada."""
        if self.session is None:
            return self._col(body, 560), None
        gap = 30
        left = pygame.Rect(body.x, body.y, int(body.width * 0.55), body.height)
        right = pygame.Rect(
            left.right + gap, body.y, body.right - (left.right + gap), body.height
        )
        return left, right

    def _draw_chat_panel(
        self, surface: pygame.Surface, area: pygame.Rect, title: str,
        log: list[tuple[str, str]],
    ) -> None:
        head = self.small_font.render(
            title, True, INK if self._on_scroll else theme.TEXT
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
        lines = self._chat_lines(log, box.width - 16)
        if not lines:
            hint = self.small_font.render("Sin mensajes todavía…", True, dim_color)
            surface.blit(hint, (box.x + 8, box.y + 8))
        else:
            line_h = 22
            max_lines = max(1, (box.height - 12) // line_h)
            ty = box.y + 8
            for ln in lines[-max_lines:]:
                surface.blit(self.small_font.render(ln, True, line_color), (box.x + 8, ty))
                ty += line_h
        field_area = pygame.Rect(box.x - 20, box.bottom + 2, box.width + 40, input_h)
        self._field(surface, "chat", "Mensaje (Enter envía)", self.f_chat, field_area, field_area.y)

    def _chat_lines(self, log: list[tuple[str, str]], max_width: int) -> list[str]:
        """Aplana el log en líneas ajustadas al ancho del panel."""
        lines: list[str] = []
        for name, text in log:
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

    def _list_row(
        self, surface, area, y, label, selected: bool, key, store, height: int = 32
    ) -> int:
        """Fila seleccionable (servidor o partida) en estilo pergamino."""
        rect = pygame.Rect(area.x + 10, y, area.width - 20, height)
        if self._on_scroll:
            overlay = pygame.Surface(rect.size, pygame.SRCALPHA)
            overlay.fill((90, 55, 20, 70 if selected else 28))
            surface.blit(overlay, rect.topleft)
            if selected:
                pygame.draw.rect(surface, INK, rect, width=2, border_radius=6)
            text_color = INK
        else:
            pygame.draw.rect(
                surface, (60, 66, 74) if selected else (40, 44, 50), rect, border_radius=6
            )
            text_color = theme.TEXT
        surface.blit(
            self.small_font.render(label, True, text_color),
            (rect.x + 10, rect.y + (height - 22) // 2),
        )
        store[key] = rect
        return rect.bottom + 6

    def _button(
        self, surface, bid, label, area, y, option: bool = False, bordered: bool = False
    ) -> int:
        """Botón en estilo "tinta" sobre el pergamino (acciones con marco;
        opciones como filas de texto). Degrada a relleno sin el pergamino."""
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
        surface.blit(self.font.render(shown, True, txt_color), (rect.x + 10, rect.y + 3))
        self._fields[fid] = rect
        return rect.bottom + 10
