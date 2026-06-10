"""Menú principal: nueva partida (con parámetros), cargar partida, salir.

La pantalla expone su decisión en `action`: None mientras el usuario navega,
o un NewGameChoice / LoadChoice / "quit" que el loop de la app consume con
`take_action()`. ESC retrocede de un submenú al menú principal; en el menú
principal equivale a Salir.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pygame

from wom.core.mapgen import MapParams
from wom.core.victory import VictoryMode
from wom.persistence.savegame import list_saves, save_info
from wom.ui import theme

# Tamaños de mapa que ofrece el menú: nombre → (ancho, alto, forts, towns).
MAP_SIZES: dict[str, tuple[int, int, int, int]] = {
    "chico": (22, 15, 3, 4),
    "medio": (30, 20, 4, 6),
    "grande": (40, 26, 6, 8),
}
AI_LEVELS = ["facil", "medio", "dificil"]
VICTORY_MODES = [VictoryMode.TOTAL, VictoryMode.FLAGS, VictoryMode.TIME]
VICTORY_LABELS = {
    VictoryMode.TOTAL: "conquista total",
    VictoryMode.FLAGS: "capturar las banderas",
    VictoryMode.TIME: "superioridad al turno límite",
}
MAX_SAVES_SHOWN = 10

BUTTON_SIZE = (380, 48)
BUTTON_GAP = 14


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

    def __init__(self, default_ai_level: str = "medio", default_seed: int | None = None):
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

    def take_action(self) -> NewGameChoice | LoadChoice | str | None:
        """Devuelve y consume la decisión del usuario (la lee el loop de app)."""
        action, self.action = self.action, None
        return action

    # --- input ---------------------------------------------------------------

    def handle_event(self, event: pygame.event.Event) -> None:
        if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
            if self.mode == "main":
                self.action = "quit"
            else:
                self.mode = "main"
        elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            self._click(event.pos)

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

    # --- dibujo ----------------------------------------------------------------

    def draw(self, surface: pygame.Surface) -> None:
        surface.fill(theme.BACKGROUND)
        self._buttons.clear()
        center_x = surface.get_rect().centerx
        title = self.title_font.render("WOM", True, theme.TEXT)
        surface.blit(title, title.get_rect(center=(center_x, 110)))
        subtitle = self.small_font.render(
            "Juego de estrategia militar por turnos", True, theme.TEXT_DIM
        )
        surface.blit(subtitle, subtitle.get_rect(center=(center_x, 160)))

        if self.mode == "main":
            self._draw_main(surface, center_x)
        elif self.mode == "new":
            self._draw_new(surface, center_x)
        else:
            self._draw_load(surface, center_x)

    def _draw_main(self, surface: pygame.Surface, center_x: int) -> None:
        y = 260
        for bid, label in (
            ("new", "Nueva partida"),
            ("load", "Cargar partida"),
            ("quit", "Salir"),
        ):
            y = self._button(surface, bid, label, center_x, y)

    def _draw_new(self, surface: pygame.Surface, center_x: int) -> None:
        y = 240
        width, height, forts, towns = MAP_SIZES[self.map_size]
        options = (
            ("ai_level", f"Nivel de la AI:  {self.ai_level}"),
            ("map_size", f"Mapa:  {self.map_size} ({width}x{height}, "
                         f"{forts} fuertes, {towns} pueblos)"),
            ("victory", f"Victoria:  {VICTORY_LABELS[self.victory_mode]}"),
        )
        for bid, label in options:
            y = self._button(surface, bid, label, center_x, y, option_style=True)
        hint = self.small_font.render("(click para cambiar cada opción)", True, theme.TEXT_DIM)
        surface.blit(hint, hint.get_rect(center=(center_x, y + 6)))
        y += 34
        y = self._button(surface, "start", "Comenzar", center_x, y)
        self._button(surface, "back", "Volver (ESC)", center_x, y, option_style=True)

    def _draw_load(self, surface: pygame.Surface, center_x: int) -> None:
        y = 220
        if not self._saves:
            label = self.font.render("No hay partidas guardadas", True, theme.TEXT_DIM)
            surface.blit(label, label.get_rect(center=(center_x, y + 20)))
            y += 60
        for info in self._saves:
            text = f"{info['name']}  —  turno {info['turn']}  —  {info['saved_at']}"
            y = self._button(
                surface, f"save:{info['path']}", text, center_x, y, option_style=True
            )
        self._button(surface, "back", "Volver (ESC)", center_x, y + 10)

    def _button(
        self,
        surface: pygame.Surface,
        bid: str,
        label: str,
        center_x: int,
        y: int,
        option_style: bool = False,
    ) -> int:
        """Dibuja un botón centrado, lo registra para hit-testing; devuelve el y siguiente."""
        rect = pygame.Rect(0, 0, *BUTTON_SIZE)
        if option_style:
            rect.width = 520
            rect.height = 40
        rect.centerx = center_x
        rect.y = y
        over = rect.collidepoint(pygame.mouse.get_pos())
        if option_style:
            bg = (50, 56, 62) if not over else (70, 78, 86)
        else:
            bg = theme.BUTTON_BG_OVER if over else theme.BUTTON_BG
        pygame.draw.rect(surface, bg, rect, border_radius=8)
        text = self.font.render(label, True, theme.TEXT)
        surface.blit(text, text.get_rect(center=rect.center))
        self._buttons[bid] = rect
        return rect.bottom + BUTTON_GAP


def _next(values: list, current) -> object:
    """Valor siguiente de la lista, cíclico."""
    return values[(values.index(current) + 1) % len(values)]
