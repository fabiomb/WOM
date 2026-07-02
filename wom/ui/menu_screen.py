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

import io
from dataclasses import dataclass
from pathlib import Path

import pygame

from wom import __version__
from wom.core.mapgen import MAP_SIZES, MapParams
from wom.core.victory import VictoryMode
from wom.core.worldmap import MAX_PLAYERS
from wom.persistence.savegame import list_saves, save_info
from wom.persistence.scenario import list_maps, load_scenario, scenario_info
from wom.persistence.settings import Settings
from wom.ui import scale, theme
from wom.ui.assets import ASSETS_DIR
from wom.ui.music import MusicPlayer
from wom.ui.video import RESOLUTIONS, apply_video_settings
from wom.ui.videoclip import VideoBackground, can_play_video

# MAP_SIZES (tamaños de mapa preestablecidos) vive en wom.core.mapgen (módulo
# puro, compartido con el servidor dedicado); se reexporta acá por compatibilidad.
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
# Video de portada opcional (data/assets/titlle-video.mp4): si hay ffmpeg y no
# es headless, reemplaza a la imagen como fondo animado en bucle; si falta, se
# usa title.png. Reutiliza la infra de ffmpeg de los videos de fin de partida.
TITLE_VIDEO = "titlle-video.mp4"
SCROLL_AREA = (0.365, 0.14, 0.615, 0.64)

# "Tinta" para los botones sobre el pergamino.
INK = (62, 40, 18)
INK_DIM = (115, 88, 55)
INK_HOVER = (146, 30, 18)


@dataclass(frozen=True)
class NewGameChoice:
    # Un nivel por rival IA (1..MAX_PLAYERS-1): la partida es humano + estas IAs.
    ai_levels: tuple[str, ...]
    map_size: str
    victory_mode: VictoryMode
    seed: int | None = None
    # Si está, la partida usa el terreno+tropas de este `.wom` ("Cargar mapa")
    # en vez de generar uno aleatorio; el tamaño se ignora.
    map_path: Path | None = None

    @property
    def n_players(self) -> int:
        return 1 + len(self.ai_levels)

    def map_params(self) -> MapParams:
        width, height, forts, towns = MAP_SIZES[self.map_size]
        # Al menos un fuerte inicial por jugador (los presets chicos traen 3).
        forts = max(forts, self.n_players)
        return MapParams(width, height, forts, towns, self.seed, n_players=self.n_players)


@dataclass(frozen=True)
class LoadChoice:
    path: Path


@dataclass(frozen=True)
class ScenarioChoice:
    """Jugar un escenario completo (honra IA y victoria del `.wom`)."""

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
        self.action: NewGameChoice | LoadChoice | ScenarioChoice | str | None = None
        default_level = default_ai_level if default_ai_level in AI_LEVELS else "medio"
        # Un nivel por rival IA; el largo de la lista es la cantidad de rivales
        # (1..MAX_PLAYERS-1). Arranca con un solo rival (partida 1 vs 1 clásica).
        self.ai_levels: list[str] = [default_level]
        self.map_size = "medio"
        self.victory_mode = VictoryMode.TOTAL
        self.seed = default_seed
        # Origen del mapa en "Nueva partida": aleatorio o un `.wom` cargado.
        self.map_source = "aleatorio"  # "aleatorio" | "archivo"
        self.loaded_map_path: Path | None = None
        self._maps: list[Path] = []        # archivos para pick_map
        self._scenarios: list[dict] = []   # info de escenarios (con título)
        self._intro_image: pygame.Surface | None = None  # imagen del escenario
        self._intro_info: dict | None = None  # título/descripción del intro
        self.title_font = pygame.font.SysFont(None, 72)
        self.font = pygame.font.SysFont(None, 30)
        self.small_font = pygame.font.SysFont(None, 22)
        self._buttons: dict[str, pygame.Rect] = {}  # id → rect (del último draw)
        self._saves: list[dict] = []
        path = ASSETS_DIR / TITLE_IMAGE
        self.background = pygame.image.load(str(path)) if path.exists() else None
        self._scaled_bg: pygame.Surface | None = None  # cache al tamaño de ventana
        self._on_scroll = False  # True si se está dibujando sobre el pergamino
        # Video de portada (perezoso: se crea en el primer draw, ya con el
        # tamaño de ventana; se recrea si la ventana cambia de tamaño).
        self.video_path = ASSETS_DIR / TITLE_VIDEO
        self._video: VideoBackground | None = None
        self._video_size: tuple[int, int] | None = None

    def take_action(self) -> NewGameChoice | LoadChoice | ScenarioChoice | str | None:
        """Devuelve y consume la decisión del usuario (la lee el loop de app)."""
        action, self.action = self.action, None
        return action

    def _open_scenario_intro(self, path: Path) -> None:
        """Carga el `.wom` y prepara la pantalla de intro (texto + imagen)."""
        try:
            doc = load_scenario(path)
        except (OSError, ValueError, KeyError):
            return
        self._intro_info = {
            "path": path,
            "title": doc.title,
            "description": doc.description,
            "victory_mode": doc.victory_mode,
            "ai_level": doc.ai_level,
        }
        self._intro_image = None
        if doc.image_bytes:
            try:
                self._intro_image = pygame.image.load(io.BytesIO(doc.image_bytes))
            except (pygame.error, OSError):
                self._intro_image = None
        self.mode = "scenario_intro"

    @property
    def capturing_text(self) -> bool:
        """True mientras se edita la carpeta de música (suspende atajos globales,
        p. ej. la M del reproductor)."""
        return self._editing_folder is not None

    # --- input ---------------------------------------------------------------

    def handle_event(self, event: pygame.event.Event) -> None:
        if self._editing_folder is not None and event.type == pygame.KEYDOWN:
            self._edit_folder_key(event)
            return
        if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
            if self.mode in ("sound", "video"):
                self.mode = "options"  # un nivel atrás: al hub de opciones
            elif self.mode == "scenario_intro":
                self.mode = "scenarios"
            elif self.mode == "pick_map":
                self.mode = "new"
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
            elif hit == "scenarios":
                self._scenarios = [
                    info for p in list_maps()
                    if (info := _scenario_info_safe(p)) is not None and info["title"].strip()
                ]
                self.mode = "scenarios"
            elif hit == "editor":
                self.action = "editor"
            elif hit == "multiplayer":
                self.action = "multiplayer"
            elif hit == "load":
                self._saves = [save_info(p) | {"path": p} for p in list_saves()[:MAX_SAVES_SHOWN]]
                self.mode = "load"
            elif hit == "options":
                self.mode = "options"
            elif hit == "quit":
                self.action = "quit"
        elif self.mode == "new":
            if hit == "n_opponents":
                self._cycle_opponents()
            elif hit.startswith("ai_level:"):
                i = int(hit.split(":", 1)[1])
                self.ai_levels[i] = _next(AI_LEVELS, self.ai_levels[i])
            elif hit == "map_size":
                self.map_size = _next(list(MAP_SIZES), self.map_size)
            elif hit == "victory":
                self.victory_mode = _next(VICTORY_MODES, self.victory_mode)
            elif hit == "map_source":
                self.map_source = "archivo" if self.map_source == "aleatorio" else "aleatorio"
            elif hit == "pick_file":
                self._maps = list_maps()
                self.mode = "pick_map"
            elif hit == "start":
                self.action = NewGameChoice(
                    tuple(self.ai_levels), self.map_size, self.victory_mode, self.seed,
                    map_path=self.loaded_map_path if self.map_source == "archivo" else None,
                )
            elif hit == "back":
                self.mode = "main"
        elif self.mode == "pick_map":
            if hit == "back":
                self.mode = "new"
            elif hit.startswith("map:"):
                self.loaded_map_path = Path(hit[len("map:"):])
                self.map_source = "archivo"
                self.mode = "new"
        elif self.mode == "scenarios":
            if hit == "back":
                self.mode = "main"
            elif hit.startswith("scn:"):
                self._open_scenario_intro(Path(hit[len("scn:"):]))
        elif self.mode == "scenario_intro":
            if hit == "play" and self._intro_info is not None:
                self.action = ScenarioChoice(self._intro_info["path"])
            elif hit == "back":
                self.mode = "scenarios"
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
        if self._draw_cover(surface, window):
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
        elif self.mode == "scenarios":
            self._draw_scenarios(surface, area)
        elif self.mode == "scenario_intro":
            self._draw_scenario_intro(surface, area)
        elif self.mode == "pick_map":
            self._draw_pick_map(surface, area)
        else:
            self._draw_load(surface, area)

    def _draw_cover(self, surface: pygame.Surface, window: pygame.Rect) -> bool:
        """Dibuja la portada: imagen (title.png) y, encima, el video en bucle si
        está disponible. Devuelve True si se pintó alguna portada (⇒ el menú va
        sobre el pergamino); False si no hay ni imagen ni video (fondo plano)."""
        drew = False
        if self.background is not None:
            if self._scaled_bg is None or self._scaled_bg.get_size() != window.size:
                self._scaled_bg = pygame.transform.smoothscale(self.background, window.size)
            surface.blit(self._scaled_bg, (0, 0))
            drew = True
        self._ensure_video(window.size)
        if self._video is not None and self._video.draw(surface, window):
            drew = True  # el frame del video tapa la imagen
        return drew

    def _ensure_video(self, size: tuple[int, int]) -> None:
        """Crea (o recrea al cambiar el tamaño de ventana) el video de portada.
        No hace nada en headless / sin ffmpeg / sin el archivo."""
        if not self.video_path.exists() or not can_play_video():
            return
        if self._video is None or self._video_size != size:
            if self._video is not None:
                self._video.close()
            self._video = VideoBackground(self.video_path, size)
            self._video_size = size

    def close(self) -> None:
        """Libera el video de portada (corta ffmpeg y su hilo). Lo llama la app
        al abandonar el menú, porque el hilo lector mantendría vivo el objeto."""
        if self._video is not None:
            self._video.close()
            self._video = None

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
            ("scenarios", "Escenarios"),
            ("editor", "Editor de mapas"),
            ("multiplayer", "Multijugador"),
            ("load", "Cargar partida"),
            ("options", "Opciones"),
            ("quit", "Salir"),
        )
        height = len(rows) * BUTTON_SIZE[1] + (len(rows) - 1) * BUTTON_GAP
        y = area.y + max(0, (area.height - height) // 2)
        for bid, label in rows:
            y = self._button(surface, bid, label, area, y)

    def _draw_new(self, surface: pygame.Surface, area: pygame.Rect) -> None:
        y = area.y + 4
        heading = self.small_font.render(
            "CONFIGURACIÓN DE PARTIDA", True, INK if self._on_scroll else theme.TEXT
        )
        surface.blit(heading, heading.get_rect(midtop=(area.centerx, y)))
        y += 26
        width, height, forts, towns = MAP_SIZES[self.map_size]
        if self.map_source == "archivo":
            name = self.loaded_map_path.stem if self.loaded_map_path else "(elegir)"
            map_row = ("pick_file", f"Archivo:  {name}")
        else:
            map_row = ("map_size", f"Mapa:  {self.map_size} ({width}x{height})")
        options = [("n_opponents", f"Rivales (IA):  {len(self.ai_levels)}")]
        for i, level in enumerate(self.ai_levels):
            options.append((f"ai_level:{i}", f"   IA {i + 1}:  {level}"))
        options += [
            ("map_source", f"Origen del mapa:  {self.map_source}"),
            map_row,
            ("victory", f"Victoria:  {VICTORY_LABELS[self.victory_mode]}"),
        ]
        for bid, label in options:
            y = self._button(surface, bid, label, area, y, option_style=True)
        hint_color = INK_DIM if self._on_scroll else theme.TEXT_DIM
        hint = self.small_font.render("(click para cambiar cada opción)", True, hint_color)
        surface.blit(hint, hint.get_rect(center=(area.centerx, y + 6)))
        # Separa la configuración de la acción de comienzo.
        y = self._divider(surface, area, y + 26)
        y += 12
        can_start = self.map_source == "aleatorio" or self.loaded_map_path is not None
        if can_start:
            y = self._button(surface, "start", "Comenzar", area, y, bordered=True)
        else:
            warn = self.small_font.render(
                "Elegí un archivo para comenzar", True, INK_HOVER if self._on_scroll else theme.TEXT_DIM
            )
            surface.blit(warn, warn.get_rect(center=(area.centerx, y + 18)))
            y += 42
        y += 4
        self._button(surface, "back", "Volver (ESC)", area, y, option_style=True)

    def _cycle_opponents(self) -> None:
        """Cicla la cantidad de rivales IA (1..MAX_PLAYERS-1), preservando los
        niveles ya elegidos y agregando 'medio' al sumar uno."""
        n = len(self.ai_levels) % (MAX_PLAYERS - 1) + 1
        if n > len(self.ai_levels):
            self.ai_levels.append("medio")
        else:
            del self.ai_levels[n:]

    def _draw_scenarios(self, surface: pygame.Surface, area: pygame.Rect) -> None:
        y = area.y + 4
        if not self._scenarios:
            color = INK_DIM if self._on_scroll else theme.TEXT_DIM
            label = self.font.render("No hay escenarios", True, color)
            surface.blit(label, label.get_rect(center=(area.centerx, y + 20)))
            y += 60
        row_height = (34 if self._on_scroll else 40) + 8
        max_rows = max(1, (area.height - BUTTON_SIZE[1] - 20) // row_height)
        for info in self._scenarios[:max_rows]:
            y = self._button(
                surface, f"scn:{info['path']}", info["title"], area, y, option_style=True
            )
        self._button(surface, "back", "Volver (ESC)", area, y + 8)

    def _draw_pick_map(self, surface: pygame.Surface, area: pygame.Rect) -> None:
        y = area.y + 4
        if not self._maps:
            color = INK_DIM if self._on_scroll else theme.TEXT_DIM
            label = self.font.render("No hay mapas en maps/", True, color)
            surface.blit(label, label.get_rect(center=(area.centerx, y + 20)))
            y += 60
        row_height = (34 if self._on_scroll else 40) + 8
        max_rows = max(1, (area.height - BUTTON_SIZE[1] - 20) // row_height)
        for path in self._maps[:max_rows]:
            y = self._button(surface, f"map:{path}", path.stem, area, y, option_style=True)
        self._button(surface, "back", "Volver (ESC)", area, y + 8)

    def _draw_scenario_intro(self, surface: pygame.Surface, area: pygame.Rect) -> None:
        info = self._intro_info or {}
        y = area.y + 6
        title = self.font.render(info.get("title", ""), True, INK if self._on_scroll else theme.TEXT)
        surface.blit(title, title.get_rect(midtop=(area.centerx, y)))
        y += 44
        if self._intro_image is not None:
            max_w, max_h = area.width - 16, area.height // 3
            img = self._intro_image
            scale_f = min(max_w / img.get_width(), max_h / img.get_height(), 1.0)
            scaled = pygame.transform.smoothscale(
                img, (round(img.get_width() * scale_f), round(img.get_height() * scale_f))
            )
            surface.blit(scaled, scaled.get_rect(midtop=(area.centerx, y)))
            y += scaled.get_height() + 12
        y = self._wrap_text(surface, info.get("description", ""), area, y)
        y += 10
        y = self._button(surface, "play", "Jugar", area, y)
        self._button(surface, "back", "Volver (ESC)", area, y, option_style=True)

    def _wrap_text(self, surface: pygame.Surface, text: str, area: pygame.Rect, y: int) -> int:
        """Dibuja `text` ajustado al ancho del área; devuelve el y siguiente."""
        color = INK if self._on_scroll else theme.TEXT_DIM
        words = text.split()
        line = ""
        for word in words:
            probe = f"{line} {word}".strip()
            if self.small_font.size(probe)[0] > area.width - 16 and line:
                rendered = self.small_font.render(line, True, color)
                surface.blit(rendered, rendered.get_rect(midtop=(area.centerx, y)))
                y += 24
                line = word
            else:
                line = probe
        if line:
            rendered = self.small_font.render(line, True, color)
            surface.blit(rendered, rendered.get_rect(midtop=(area.centerx, y)))
            y += 24
        return y

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

    def _divider(self, surface: pygame.Surface, area: pygame.Rect, y: int) -> int:
        """Línea horizontal de separación (tinta translúcida sobre el pergamino).
        Devuelve el y siguiente."""
        x0, x1 = area.x + 20, area.right - 20
        if self._on_scroll:
            line = pygame.Surface((max(1, x1 - x0), 2), pygame.SRCALPHA)
            line.fill((*INK, 120))
            surface.blit(line, (x0, y))
        else:
            pygame.draw.line(surface, theme.TEXT_DIM, (x0, y), (x1, y), 1)
        return y + 2

    def _button(
        self,
        surface: pygame.Surface,
        bid: str,
        label: str,
        area: pygame.Rect,
        y: int,
        option_style: bool = False,
        bordered: bool = False,
    ) -> int:
        """Dibuja un botón centrado en el área, lo registra para hit-testing;
        devuelve el y siguiente. Sobre el pergamino el estilo es de "tinta"
        (texto oscuro, realce translúcido al pasar el mouse). Con `bordered` el
        botón lleva un marco de tinta (sin relleno) para destacar la acción
        principal, aprovechando el color que ya aporta el pergamino."""
        rect = pygame.Rect(0, 0, *BUTTON_SIZE)
        if option_style:
            rect.width = 520
            rect.height = 40
        if self._on_scroll:
            # Los botones con borde van más angostos (80%) para que el marco no
            # se pegue a los bordes del pergamino.
            rect.width = round((area.width - 8) * (0.8 if bordered else 1.0))
            rect.height = 34 if option_style else 42
        rect.centerx = area.centerx
        rect.y = y
        over = rect.collidepoint(scale.mouse_pos())
        font = self.small_font if (option_style and self._on_scroll) else self.font
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


def _scenario_info_safe(path: Path) -> dict | None:
    """scenario_info tolerante: descarta archivos `.wom` corruptos o ajenos."""
    try:
        return scenario_info(path)
    except (OSError, ValueError, KeyError):
        return None
