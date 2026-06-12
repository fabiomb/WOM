"""Menú principal: nueva partida (con parámetros), cargar, opciones, salir.

La pantalla expone su decisión en `action`: None mientras el usuario navega,
o un NewGameChoice / LoadChoice / "quit" que el loop de la app consume con
`take_action()`. ESC retrocede de un submenú al menú principal; en el menú
principal equivale a Salir.

Opciones es un hub con dos submenús: Sonido (música on/off, volumen,
carpeta — editable escribiendo en el propio botón — y orden) y Video
(resolución de ventana, iniciar maximizado). Operan a través del MusicPlayer
que le pasa la app; cada cambio se aplica y persiste al instante
(settings.json). ESC retrocede de a un nivel: submenú → Opciones → main.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pygame

from wom import __version__
from wom.core.mapgen import MapParams
from wom.core.victory import VictoryMode
from wom.persistence.savegame import list_saves, save_info
from wom.persistence.settings import Settings
from wom.ui import scale, theme
from wom.ui.assets import ASSETS_DIR
from wom.ui.music import MusicPlayer
from wom.ui.video import RESOLUTIONS, apply_video_settings

# Tamaños de mapa que ofrece el menú: nombre → (ancho, alto, forts, towns).
# Con el zoom de la rueda los mapas grandes son navegables aunque el tile
# base quede chico (la vista inicial siempre encuadra el mapa entero).
MAP_SIZES: dict[str, tuple[int, int, int, int]] = {
    "chico": (22, 15, 3, 4),
    "medio": (30, 20, 4, 6),
    "grande": (44, 29, 6, 9),
    "super grande": (60, 40, 9, 14),
}
AI_LEVELS = ["facil", "medio", "dificil"]
VICTORY_MODES = [VictoryMode.TOTAL, VictoryMode.FLAGS, VictoryMode.TIME]
VICTORY_LABELS = {
    VictoryMode.TOTAL: "conquista total",
    VictoryMode.FLAGS: "capturar banderas",
    VictoryMode.TIME: "límite de turnos",
}
MAX_SAVES_SHOWN = 10

BUTTON_SIZE = (380, 48)
BUTTON_GAP = 14

# Portada (data/assets/title.png): se estira a la ventana y el menú se dibuja
# dentro del pergamino central. Zona útil del pergamino como fracciones del
# ancho/alto de la imagen (x0, y0, x1, y1). Si el asset no existe, el menú
# cae al fondo plano de siempre.
TITLE_IMAGE = "title.png"
SCROLL_AREA = (0.365, 0.14, 0.615, 0.64)

# "Tinta" para los botones sobre el pergamino.
INK = (62, 40, 18)
INK_DIM = (115, 88, 55)
INK_HOVER = (146, 30, 18)


@dataclass(frozen=True)
class NewGameChoice:
    ai_level: str
    map_size: str
    victory_mode: VictoryMode
    seed: int | None = None

    def map_params(self) -> MapParams:
        width, height, forts, towns = MAP_SIZES[self.map_size]
        return MapParams(width, height, forts, towns, self.seed)


@dataclass(frozen=True)
class LoadChoice:
    path: Path


class MenuScreen:
    """Menú con tres modos internos: main, new (opciones) y load (saves)."""

    def __init__(
        self,
        default_ai_level: str = "medio",
        default_seed: int | None = None,
        music: MusicPlayer | None = None,
    ):
        self.music = music  # None en tests sin audio: Opciones queda inerte
        self._editing_folder: str | None = None  # buffer mientras se escribe
        self.mode = "main"
        self.action: NewGameChoice | LoadChoice | str | None = None
        self.ai_level = default_ai_level if default_ai_level in AI_LEVELS else "medio"
        self.map_size = "medio"
        self.victory_mode = VictoryMode.TOTAL
        self.seed = default_seed
        self.title_font = pygame.font.SysFont(None, 72)
        self.font = pygame.font.SysFont(None, 30)
        self.small_font = pygame.font.SysFont(None, 22)
        self._buttons: dict[str, pygame.Rect] = {}  # id → rect (del último draw)
        self._saves: list[dict] = []
        path = ASSETS_DIR / TITLE_IMAGE
        self.background = pygame.image.load(str(path)) if path.exists() else None
        self._scaled_bg: pygame.Surface | None = None  # cache al tamaño de ventana
        self._on_scroll = False  # True si se está dibujando sobre el pergamino

    def take_action(self) -> NewGameChoice | LoadChoice | str | None:
        """Devuelve y consume la decisión del usuario (la lee el loop de app)."""
        action, self.action = self.action, None
        return action

    # --- input ---------------------------------------------------------------

    def handle_event(self, event: pygame.event.Event) -> None:
        if self._editing_folder is not None and event.type == pygame.KEYDOWN:
            self._edit_folder_key(event)
            return
        if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
            if self.mode in ("sound", "video"):
                self.mode = "options"  # un nivel atrás: al hub de opciones
            elif self.mode == "main":
                self.action = "quit"
            else:
                self.mode = "main"
        elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            self._click(event.pos)

    def _edit_folder_key(self, event: pygame.event.Event) -> None:
        """Edición de la carpeta de música escribiendo sobre el botón."""
        if event.key in (pygame.K_RETURN, pygame.K_KP_ENTER):
            if self.music is not None:
                self.music.set_folder(self._editing_folder)
            self._editing_folder = None
        elif event.key == pygame.K_ESCAPE:
            self._editing_folder = None  # cancela sin aplicar
        elif event.key == pygame.K_BACKSPACE:
            self._editing_folder = self._editing_folder[:-1]
        elif event.unicode and event.unicode.isprintable():
            self._editing_folder += event.unicode

    def _click(self, point: tuple[int, int]) -> None:
        hit = next(
            (bid for bid, rect in self._buttons.items() if rect.collidepoint(point)),
            None,
        )
        if hit is None:
            return
        if self.mode == "main":
            if hit == "new":
                self.mode = "new"
            elif hit == "load":
                self._saves = [save_info(p) | {"path": p} for p in list_saves()[:MAX_SAVES_SHOWN]]
                self.mode = "load"
            elif hit == "options":
                self.mode = "options"
            elif hit == "quit":
                self.action = "quit"
        elif self.mode == "new":
            if hit == "ai_level":
                self.ai_level = _next(AI_LEVELS, self.ai_level)
            elif hit == "map_size":
                self.map_size = _next(list(MAP_SIZES), self.map_size)
            elif hit == "victory":
                self.victory_mode = _next(VICTORY_MODES, self.victory_mode)
            elif hit == "start":
                self.action = NewGameChoice(
                    self.ai_level, self.map_size, self.victory_mode, self.seed
                )
            elif hit == "back":
                self.mode = "main"
        elif self.mode == "load":
            if hit == "back":
                self.mode = "main"
            elif hit.startswith("save:"):
                self.action = LoadChoice(Path(hit[len("save:"):]))
        elif self.mode == "options":
            if hit in ("sound", "video"):
                self.mode = hit
            elif hit == "back":
                self.mode = "main"
        elif self.mode == "sound":
            self._click_sound(hit)
        elif self.mode == "video":
            self._click_video(hit)

    def _click_sound(self, hit: str) -> None:
        if hit == "back":
            self.mode = "options"
            return
        if self.music is None:
            return  # sin reproductor (tests headless): las opciones no aplican
        settings = self.music.settings
        if hit == "music_toggle":
            self.music.set_enabled(not settings.music_enabled)
        elif hit == "music_volume":
            # cíclico de a 10%: ...90% → 100% → 0%
            new = settings.music_volume + 0.1
            self.music.set_volume(0.0 if new > 1.001 else new)
        elif hit == "music_folder":
            self._editing_folder = settings.music_folder
        elif hit == "music_order":
            self.music.set_shuffle(not settings.music_shuffle)

    def _click_video(self, hit: str) -> None:
        if hit == "back":
            self.mode = "options"
            return
        if self.music is None:
            return
        settings = self.music.settings
        if hit == "video_res":
            current = settings.video_resolution
            settings.video_resolution = (
                _next(RESOLUTIONS, current) if current in RESOLUTIONS else RESOLUTIONS[0]
            )
            self.music.save()
            apply_video_settings(settings)  # se ve el cambio al instante
        elif hit == "video_max":
            settings.video_maximized = not settings.video_maximized
            self.music.save()
            apply_video_settings(settings)

    # --- dibujo ----------------------------------------------------------------

    def draw(self, surface: pygame.Surface) -> None:
        self._buttons.clear()
        window = surface.get_rect()
        if self.background is not None:
            if self._scaled_bg is None or self._scaled_bg.get_size() != window.size:
                self._scaled_bg = pygame.transform.smoothscale(self.background, window.size)
            surface.blit(self._scaled_bg, (0, 0))
            x0, y0, x1, y1 = SCROLL_AREA
            area = pygame.Rect(
                round(window.width * x0), round(window.height * y0),
                round(window.width * (x1 - x0)), round(window.height * (y1 - y0)),
            )
            self._on_scroll = True
        else:
            surface.fill(theme.BACKGROUND)
            title = self.title_font.render("WOM", True, theme.TEXT)
            surface.blit(title, title.get_rect(center=(window.centerx, 110)))
            subtitle = self.small_font.render(
                "Juego de estrategia militar por turnos", True, theme.TEXT_DIM
            )
            surface.blit(subtitle, subtitle.get_rect(center=(window.centerx, 160)))
            area = pygame.Rect(0, 210, 560, window.height - 270)
            area.centerx = window.centerx
            self._on_scroll = False

        self._draw_version(surface, window)

        if self.mode == "main":
            self._draw_main(surface, area)
        elif self.mode == "new":
            self._draw_new(surface, area)
        elif self.mode == "options":
            self._draw_options(surface, area)
        elif self.mode == "sound":
            self._draw_sound(surface, area)
        elif self.mode == "video":
            self._draw_video(surface, area)
        else:
            self._draw_load(surface, area)

    def _draw_version(self, surface: pygame.Surface, window: pygame.Rect) -> None:
        """Número de versión abajo a la derecha, con sombra para que se lea
        sobre cualquier zona de la portada."""
        text = f"v{__version__}"
        shadow = self.small_font.render(text, True, (20, 14, 8))
        label = self.small_font.render(text, True, (228, 218, 196))
        pos = label.get_rect(bottomright=(window.right - 10, window.bottom - 8))
        surface.blit(shadow, pos.move(1, 1))
        surface.blit(label, pos)

    def _draw_main(self, surface: pygame.Surface, area: pygame.Rect) -> None:
        rows = (
            ("new", "Nueva partida"),
            ("load", "Cargar partida"),
            ("options", "Opciones"),
            ("quit", "Salir"),
        )
        height = len(rows) * BUTTON_SIZE[1] + (len(rows) - 1) * BUTTON_GAP
        y = area.y + max(0, (area.height - height) // 2)
        for bid, label in rows:
            y = self._button(surface, bid, label, area, y)

    def _draw_new(self, surface: pygame.Surface, area: pygame.Rect) -> None:
        y = area.y + 6
        width, height, forts, towns = MAP_SIZES[self.map_size]
        options = (
            ("ai_level", f"Nivel de la AI:  {self.ai_level}"),
            ("map_size", f"Mapa:  {self.map_size} ({width}x{height})"),
            ("victory", f"Victoria:  {VICTORY_LABELS[self.victory_mode]}"),
        )
        for bid, label in options:
            y = self._button(surface, bid, label, area, y, option_style=True)
        hint_color = INK_DIM if self._on_scroll else theme.TEXT_DIM
        hint = self.small_font.render("(click para cambiar cada opción)", True, hint_color)
        surface.blit(hint, hint.get_rect(center=(area.centerx, y + 6)))
        y += 30
        y = self._button(surface, "start", "Comenzar", area, y)
        self._button(surface, "back", "Volver (ESC)", area, y, option_style=True)

    def _draw_options(self, surface: pygame.Surface, area: pygame.Rect) -> None:
        """Hub de opciones: elegir la categoría a configurar."""
        rows = (("sound", "Sonido"), ("video", "Video"), ("back", "Volver (ESC)"))
        height = len(rows) * BUTTON_SIZE[1] + (len(rows) - 1) * BUTTON_GAP
        y = area.y + max(0, (area.height - height) // 2)
        for bid, label in rows:
            y = self._button(surface, bid, label, area, y)

    def _draw_sound(self, surface: pygame.Surface, area: pygame.Rect) -> None:
        settings = self.music.settings if self.music is not None else Settings()
        if self._editing_folder is not None:
            folder_label = f"Carpeta:  {self._editing_folder}_"
        else:
            folder_label = f"Carpeta:  {settings.music_folder}"
        rows = (
            ("music_toggle", f"Música:  {'sí' if settings.music_enabled else 'no'}"),
            ("music_volume", f"Volumen:  {round(settings.music_volume * 100)}%"),
            ("music_folder", folder_label),
            ("music_order",
             f"Orden:  {'aleatorio' if settings.music_shuffle else 'secuencial'}"),
        )
        hint = (
            "(escribí la ruta y Enter)" if self._editing_folder is not None
            else "(click para cambiar · M abre el reproductor)"
        )
        self._draw_option_rows(surface, area, rows, hint)

    def _draw_video(self, surface: pygame.Surface, area: pygame.Rect) -> None:
        settings = self.music.settings if self.music is not None else Settings()
        rows = (
            ("video_res", f"Resolución:  {settings.video_resolution}"),
            ("video_max",
             f"Iniciar maximizado:  {'sí' if settings.video_maximized else 'no'}"),
        )
        self._draw_option_rows(surface, area, rows, "(click para cambiar)")

    def _draw_option_rows(
        self,
        surface: pygame.Surface,
        area: pygame.Rect,
        rows: tuple[tuple[str, str], ...],
        hint_text: str,
    ) -> None:
        """Filas de un submenú de opciones + hint + Volver."""
        y = area.y + 6
        for bid, label in rows:
            y = self._button(surface, bid, label, area, y, option_style=True)
        hint_color = INK_DIM if self._on_scroll else theme.TEXT_DIM
        hint = self.small_font.render(hint_text, True, hint_color)
        surface.blit(hint, hint.get_rect(center=(area.centerx, y + 6)))
        y += 30
        self._button(surface, "back", "Volver (ESC)", area, y)

    def _draw_load(self, surface: pygame.Surface, area: pygame.Rect) -> None:
        y = area.y + 4
        if not self._saves:
            color = INK_DIM if self._on_scroll else theme.TEXT_DIM
            label = self.font.render("No hay partidas guardadas", True, color)
            surface.blit(label, label.get_rect(center=(area.centerx, y + 20)))
            y += 60
        # Solo las entradas que entran en el área (el pergamino tiene su altura).
        row_height = (34 if self._on_scroll else 40) + 8
        max_rows = max(1, (area.height - BUTTON_SIZE[1] - 20) // row_height)
        for info in self._saves[:max_rows]:
            text = f"{info['name']}  —  turno {info['turn']}"
            y = self._button(
                surface, f"save:{info['path']}", text, area, y, option_style=True
            )
        self._button(surface, "back", "Volver (ESC)", area, y + 8)

    def _button(
        self,
        surface: pygame.Surface,
        bid: str,
        label: str,
        area: pygame.Rect,
        y: int,
        option_style: bool = False,
    ) -> int:
        """Dibuja un botón centrado en el área, lo registra para hit-testing;
        devuelve el y siguiente. Sobre el pergamino el estilo es de "tinta"
        (texto oscuro, realce translúcido al pasar el mouse)."""
        rect = pygame.Rect(0, 0, *BUTTON_SIZE)
        if option_style:
            rect.width = 520
            rect.height = 40
        if self._on_scroll:
            rect.width = area.width - 8
            rect.height = 34 if option_style else 42
        rect.centerx = area.centerx
        rect.y = y
        over = rect.collidepoint(scale.mouse_pos())
        font = self.small_font if (option_style and self._on_scroll) else self.font
        if self._on_scroll:
            if over:
                highlight = pygame.Surface(rect.size, pygame.SRCALPHA)
                highlight.fill((90, 55, 20, 45))
                surface.blit(highlight, rect.topleft)
            text = font.render(label, True, INK_HOVER if over else INK)
        else:
            if option_style:
                bg = (50, 56, 62) if not over else (70, 78, 86)
            else:
                bg = theme.BUTTON_BG_OVER if over else theme.BUTTON_BG
            pygame.draw.rect(surface, bg, rect, border_radius=8)
            text = font.render(label, True, theme.TEXT)
        surface.blit(text, text.get_rect(center=rect.center))
        self._buttons[bid] = rect
        return rect.bottom + (8 if self._on_scroll else BUTTON_GAP)


def _next(values: list, current) -> object:
    """Valor siguiente de la lista, cíclico."""
    return values[(values.index(current) + 1) % len(values)]
