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
from wom.net.rules import MatchRules
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
from wom.ui.multiplayer_screen import NetGameStart, TextField

CONNECT_TIMEOUT = 4.0
CHAT_LOG_MAX = 8


class ServerBrowserScreen:
    """Lista de servidores + conexión + lobby (multijugador por Internet)."""

    def __init__(self, settings_path=None, session: ServerSession | None = None):
        # `session` viva ⇒ se retoma el lobby tras una partida (no se reconecta).
        self.mode = "lobby" if session is not None else "browser"
        self.wants_menu = False
        self._settings_path = settings_path

        self.title_font = pygame.font.SysFont(None, 64)
        self.font = pygame.font.SysFont(None, 30)
        self.small_font = pygame.font.SysFont(None, 22)
        self._buttons: dict[str, pygame.Rect] = {}
        self._fields: dict[str, pygame.Rect] = {}
        self._rows: dict[int, pygame.Rect] = {}
        self.focused: str | None = None

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
            self.session.send_chat(text)  # chat de la sala/partida
        else:
            self.session.send_lobby_chat(text)  # chat global del lobby
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
        surface.fill(theme.BACKGROUND)
        window = surface.get_rect()
        title = self.title_font.render("Servidores", True, theme.TEXT)
        surface.blit(title, title.get_rect(center=(window.centerx, 50)))
        width = 760 if self.mode in ("lobby", "room") else 640
        area = pygame.Rect(0, 110, width, window.height - 160)
        area.centerx = window.centerx
        {
            "browser": self._draw_browser,
            "form": self._draw_form,
            "connecting": self._draw_lobby,
            "lobby": self._draw_lobby,
            "createform": self._draw_createform,
            "room": self._draw_room,
        }[self.mode](surface, area)
        if self.status:
            self._draw_status(surface, window)

    def _draw_status(self, surface: pygame.Surface, window: pygame.Rect) -> None:
        """Línea de estado. Si es un error/rechazo, va en una barra resaltada
        (rojo) para que el motivo no pase desapercibido."""
        if self.status_error:
            label = self.font.render(self.status, True, (255, 235, 230))
            box = label.get_rect(center=(window.centerx, window.bottom - 40))
            box.inflate_ip(40, 16)
            pygame.draw.rect(surface, (150, 50, 45), box, border_radius=8)
            pygame.draw.rect(surface, (210, 90, 80), box, width=2, border_radius=8)
            surface.blit(label, label.get_rect(center=box.center))
        else:
            label = self.small_font.render(self.status, True, theme.TEXT_DIM)
            surface.blit(label, label.get_rect(center=(window.centerx, window.bottom - 30)))

    def _draw_browser(self, surface: pygame.Surface, area: pygame.Rect) -> None:
        y = self._field(surface, "player", "Tu nombre", self.f_player, area, area.y)
        y += 6
        caption = self.small_font.render("Servidores guardados", True, theme.TEXT_DIM)
        surface.blit(caption, (area.x + 40, y))
        y += 26
        if not self.servers:
            empty = self.small_font.render("(ninguno — agregá uno)", True, theme.TEXT_DIM)
            surface.blit(empty, (area.x + 50, y))
            y += 30
        for i, s in enumerate(self.servers):
            rect = pygame.Rect(area.x + 40, y, area.width - 80, 34)
            selected = i == self.selected
            pygame.draw.rect(surface, (60, 66, 74) if selected else (40, 44, 50), rect, border_radius=6)
            label = f"{s.get('name', '')}   {s.get('host', '')}:{s.get('port', '')}"
            surface.blit(self.font.render(label, True, theme.TEXT), (rect.x + 10, rect.y + 4))
            self._rows[i] = rect
            y += 40
        y += 8
        y = self._button(surface, "connect", "Conectar", area, y)
        y = self._button(surface, "add", "Agregar", area, y, option=True)
        if self.servers:
            y = self._button(surface, "edit", "Editar", area, y, option=True)
            y = self._button(surface, "delete", "Borrar", area, y, option=True)
        self._button(surface, "back", "Volver (ESC)", area, y + 6, option=True)

    def _draw_form(self, surface: pygame.Surface, area: pygame.Rect) -> None:
        y = area.y + 6
        head = "Editar servidor" if self._edit_index is not None else "Nuevo servidor"
        label = self.font.render(head, True, theme.SELECTION)
        surface.blit(label, label.get_rect(midtop=(area.centerx, y)))
        y += 50
        y = self._field(surface, "sname", "Nombre (opcional)", self.f_sname, area, y)
        y = self._field(surface, "shost", "Dirección (IP o dominio)", self.f_shost, area, y)
        y = self._field(surface, "sport", "Puerto", self.f_sport, area, y)
        y = self._button(surface, "save_form", "Guardar", area, y + 8)
        self._button(surface, "cancel_form", "Cancelar", area, y, option=True)

    def _draw_lobby(self, surface: pygame.Surface, area: pygame.Rect) -> None:
        y = area.y + 6
        name = self.session.server_name if self.session is not None else ""
        head = self.font.render(f"Lobby — {name}" if name else "Conectando…", True, theme.SELECTION)
        surface.blit(head, head.get_rect(midtop=(area.centerx, y)))
        y += 42
        # Jugadores conectados (línea compacta).
        players = self.session.players if self.session is not None else []
        nicks = ", ".join(p[1] for p in players) or "(esperando…)"
        line = self.small_font.render(f"Conectados: {nicks}", True, theme.TEXT_DIM)
        surface.blit(line, (area.x + 30, y))
        y += 30
        # Catálogo de partidas.
        cap = self.small_font.render("Partidas disponibles", True, theme.TEXT_DIM)
        surface.blit(cap, (area.x + 30, y))
        y += 26
        matches = self.session.matches if self.session is not None else []
        if not matches:
            empty = self.small_font.render("(ninguna — creá una)", True, theme.TEXT_DIM)
            surface.blit(empty, (area.x + 40, y))
            y += 30
        for row in matches:
            mid, mname, maxp, occ, estado, mapa = row[0], row[1], row[2], row[3], row[4], row[5]
            rect = pygame.Rect(area.x + 30, y, area.width - 60, 32)
            selected = mid == self.selected_match
            pygame.draw.rect(surface, (60, 66, 74) if selected else (40, 44, 50), rect, border_radius=6)
            label = f"{mname}    {occ}/{maxp}    {estado}    {mapa}"
            surface.blit(self.small_font.render(label, True, theme.TEXT), (rect.x + 10, rect.y + 6))
            self._match_rows[mid] = rect
            y += 38
        y += 6
        y = self._button(surface, "open_create", "Crear partida", area, y, option=True)
        if self.selected_match is not None:
            y = self._button(surface, "join", "Unirse a la seleccionada", area, y, option=True)
        # Chat global.
        cap2 = self.small_font.render("Chat", True, theme.TEXT_DIM)
        surface.blit(cap2, (area.x + 30, y + 4))
        y += 28
        for who, text in self.chat_log[-5:]:
            chat_line = self.small_font.render(f"{who}: {text}", True, theme.TEXT)
            surface.blit(chat_line, (area.x + 40, y))
            y += 22
        y = self._field(surface, "chat", "Mensaje (Enter envía)", self.f_chat, area, y + 2)
        self._button(surface, "disconnect", "Desconectar", area, y, option=True)

    def _draw_createform(self, surface: pygame.Surface, area: pygame.Rect) -> None:
        y = area.y + 6
        head = self.font.render("Crear partida", True, theme.SELECTION)
        surface.blit(head, head.get_rect(midtop=(area.centerx, y)))
        y += 50
        y = self._field(surface, "mname", "Nombre de la partida", self.f_mname, area, y)
        y = self._button(surface, "cmplayers", f"Jugadores:  {self.create_players}", area, y, option=True)
        w, h, _f, _t = MAP_SIZES[self.create_size]
        y = self._button(surface, "cmsize", f"Mapa:  {self.create_size} ({w}x{h})", area, y, option=True)
        y = self._field(surface, "mmaxturns", "Turnos máximos", self.f_mmaxturns, area, y)
        y = self._field(surface, "mturnsecs", "Segundos por turno (0 = sin límite)", self.f_mturnsecs, area, y)
        y = self._button(surface, "create_do", "Crear", area, y + 8)
        self._button(surface, "cancel_create", "Cancelar", area, y, option=True)

    def _draw_room(self, surface: pygame.Surface, area: pygame.Rect) -> None:
        y = area.y + 10
        head = self.font.render("Sala de espera", True, theme.SELECTION)
        surface.blit(head, head.get_rect(midtop=(area.centerx, y)))
        y += 50
        info = next((r for r in (self.session.matches if self.session else []) if r[0] == self.match_id), None)
        if info is not None:
            line = f"{info[1]} — {info[3]}/{info[2]} jugadores — {info[4]}"
            surface.blit(self.font.render(line, True, theme.TEXT), (area.x + 40, y))
            y += 40
        seat_txt = f"Tu asiento: J{(self.seat or 0) + 1}"
        surface.blit(self.small_font.render(seat_txt, True, theme.TEXT_DIM), (area.x + 40, y))
        y += 30
        wait = "Esperando a que se sumen y todos estén listos…" if not self.local_ready else "Listo. Esperando al resto…"
        surface.blit(self.small_font.render(wait, True, theme.TEXT_DIM), (area.x + 40, y))
        y += 34
        y = self._button(surface, "ready", "Cancelar listo" if self.local_ready else "¡Listo!", area, y)
        y = self._button(surface, "leaveroom", "Salir de la sala", area, y, option=True)
        # Chat de la sala (entre quienes se sumaron, hasta arrancar).
        cap = self.small_font.render("Chat de la sala", True, theme.TEXT_DIM)
        surface.blit(cap, (area.x + 40, y + 6))
        y += 30
        for who, text in self.room_chat_log[-5:]:
            chat_line = self.small_font.render(f"{who}: {text}", True, theme.TEXT)
            surface.blit(chat_line, (area.x + 50, y))
            y += 22
        self._field(surface, "chat", "Mensaje (Enter envía)", self.f_chat, area, y + 2)

    # --- helpers de dibujo ----------------------------------------------

    def _button(self, surface, bid, label, area, y, option: bool = False) -> int:
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
        pygame.draw.rect(surface, theme.TEXT if focused else (90, 96, 104), rect, width=2, border_radius=6)
        shown = field.value + ("_" if focused else "")
        surface.blit(self.font.render(shown, True, theme.TEXT), (rect.x + 10, rect.y + 4))
        self._fields[fid] = rect
        return rect.bottom + 14
