"""Ayuda visual rápida que se abre con F1 durante la partida.

Versión simplificada de `docs/como_jugar.md`: un panel modal que explica de un
vistazo los elementos del juego (tropas, terreno, sitios, victoria) y por dónde
empezar. Lee los datos en vivo de la partida (`game.classes`, `game.config`,
`game.victory_mode`) para no desincronizarse de la config de balance.

Solo dibuja y maneja el toggle; no toca el estado de la partida. La pantalla de
juego lo abre/cierra con F1 y, mientras está visible, le manda los eventos para
que ESC, F1 o un clic lo cierren y el resto del input no se filtre al mapa.
"""

from __future__ import annotations

import pygame

from wom.core.game import Game
from wom.core.victory import VictoryMode
from wom.core.worldmap import Terrain
from wom.ui import theme
from wom.ui.assets import load_scaled

# Tamaños de los sprites reales (mismos PNG que el mapa) que se muestran en la
# ayuda, para que el jugador identifique cada elemento tal como lo ve en juego.
UNIT_SPRITE = 44
TILE_SPRITE = 40
TERRAIN_NAMES = {
    Terrain.PLAINS: "Llanura",
    Terrain.FOREST: "Bosque",
    Terrain.FOREST_LIGHT: "Bosque ralo",
    Terrain.MOUNTAIN: "Montaña",
    Terrain.MOUNTAIN_LIGHT: "Colina",
    Terrain.MARSH: "Pantano",
    Terrain.WATER: "Agua",
}
# Detalle táctico extra que se muestra junto al costo de movimiento en la
# leyenda del mapa (las variantes livianas y el pantano tienen su gracia).
TERRAIN_DETAIL = {
    Terrain.FOREST_LIGHT: "cubre al partisano; estorba a caballero y arquero",
    Terrain.MOUNTAIN_LIGHT: "favorece a arquero y partisano",
    Terrain.MARSH: "frena a todos salvo al partisano (que además pelea mejor)",
}
VICTORY_LABELS = {
    VictoryMode.TOTAL: ("Total", "Eliminá todos los ejércitos y fuertes del rival."),
    VictoryMode.FLAGS: ("Banderas", "Controlá todas las banderas (fuertes y pueblos marcados)."),
    VictoryMode.TIME: ("Tiempo", "Al turno límite gana quien tenga más territorio y tropas."),
}

PANEL = (28, 32, 38)
PANEL_BORDER = (90, 100, 110)
HEADER_BG = (44, 50, 58)
ROW_ALT = (36, 41, 47)
ACCENT = (250, 220, 60)


class HelpOverlay:
    """Panel de ayuda modal. `visible` lo controla la pantalla de juego."""

    def __init__(self) -> None:
        self.visible = False
        self.title_font = pygame.font.SysFont(None, 44)
        self.h_font = pygame.font.SysFont(None, 30)
        self.font = pygame.font.SysFont(None, 24)
        self.small_font = pygame.font.SysFont(None, 20)
        # Sprites reales (los mismos PNG del mapa), cargados perezosamente en el
        # primer dibujo y cacheados. Las claves de unidades salen de la config.
        self._units: dict[str, pygame.Surface] = {}
        self._terrain: dict[Terrain, pygame.Surface] = {}
        self._sites: dict[str, pygame.Surface] = {}

    def _ensure_sprites(self, game: Game) -> None:
        if self._terrain:
            return
        self._terrain = {
            t: load_scaled(t.value, TILE_SPRITE)
            for t in (
                Terrain.PLAINS, Terrain.FOREST, Terrain.FOREST_LIGHT,
                Terrain.MOUNTAIN, Terrain.MOUNTAIN_LIGHT, Terrain.MARSH, Terrain.WATER,
            )
        }
        self._units = {cid: load_scaled(cid, UNIT_SPRITE) for cid in game.classes}
        self._sites = {name: load_scaled(name, TILE_SPRITE) for name in ("fort", "town")}

    def toggle(self) -> None:
        self.visible = not self.visible

    def handle_event(self, event: pygame.event.Event) -> bool:
        """Mientras está visible consume todo el input. Devuelve True si el
        evento fue para el overlay (la pantalla de juego no debe procesarlo)."""
        if not self.visible:
            return False
        if event.type == pygame.KEYDOWN and event.key in (
            pygame.K_F1, pygame.K_ESCAPE, pygame.K_RETURN, pygame.K_SPACE
        ):
            self.visible = False
        elif event.type == pygame.MOUSEBUTTONDOWN:
            self.visible = False
        return True  # modal: traga el resto (movimientos de mouse, etc.)

    # --- dibujo --------------------------------------------------------------

    def draw(self, surface: pygame.Surface, game: Game) -> None:
        if not self.visible:
            return
        self._ensure_sprites(game)
        w, h = surface.get_size()
        dim = pygame.Surface((w, h), pygame.SRCALPHA)
        dim.fill((0, 0, 0, 200))
        surface.blit(dim, (0, 0))

        panel = pygame.Rect(0, 0, 1580, 940)
        panel.center = (w // 2, h // 2)
        pygame.draw.rect(surface, PANEL, panel, border_radius=12)
        pygame.draw.rect(surface, PANEL_BORDER, panel, 2, border_radius=12)

        title = self.title_font.render("Cómo jugar — ayuda rápida", True, ACCENT)
        surface.blit(title, (panel.x + 36, panel.y + 26))
        hint = self.small_font.render(
            "F1 / ESC / clic para cerrar", True, theme.TEXT_DIM
        )
        surface.blit(hint, hint.get_rect(topright=(panel.right - 36, panel.y + 36)))

        top = panel.y + 92
        col_gap = 40
        left = pygame.Rect(panel.x + 36, top, 880, 560)
        right = pygame.Rect(left.right + col_gap, top, panel.right - 36 - (left.right + col_gap), 560)

        self._draw_troops(surface, game, left)
        bottom_of_right = self._draw_map_and_sites(surface, game, right)
        self._draw_victory(surface, game, pygame.Rect(right.x, bottom_of_right + 16, right.width, 0))

        self._draw_start_here(
            surface, pygame.Rect(panel.x + 36, top + 580, panel.width - 72, 0)
        )

    # --- secciones -----------------------------------------------------------

    def _section_title(self, surface, text, x, y) -> int:
        label = self.h_font.render(text, True, theme.TEXT)
        surface.blit(label, (x, y))
        pygame.draw.line(
            surface, PANEL_BORDER, (x, y + 32), (x + 360, y + 32), 1
        )
        return y + 44

    def _draw_troops(self, surface, game: Game, rect: pygame.Rect) -> None:
        y = self._section_title(surface, "Tipos de tropa", rect.x, rect.y)
        # Cabecera de columnas.
        cols = [
            ("Clase", rect.x + 56),
            ("Vel", rect.x + 300),
            ("Atq", rect.x + 370),
            ("Def", rect.x + 440),
            ("Brilla en", rect.x + 520),
            ("Vence a", rect.x + 690),
        ]
        head = pygame.Rect(rect.x, y, rect.width, 30)
        pygame.draw.rect(surface, HEADER_BG, head)
        for text, cx in cols:
            surface.blit(self.small_font.render(text, True, theme.TEXT_DIM), (cx, y + 7))
        y += 34

        # Orden de presentación fijo (de más liviano a más pesado).
        order = ["partisano", "soldado", "caballero", "arquero"]
        ids = [c for c in order if c in game.classes] + [
            c for c in game.classes if c not in order
        ]
        for i, cid in enumerate(ids):
            unit = game.classes[cid]
            row = pygame.Rect(rect.x, y, rect.width, 54)
            if i % 2:
                pygame.draw.rect(surface, ROW_ALT, row)
            # Sprite real de la clase (el mismo del mapa).
            sprite = self._units.get(cid)
            if sprite is not None:
                surface.blit(sprite, (rect.x + 6, y + (56 - UNIT_SPRITE) // 2))

            surface.blit(self.font.render(unit.nombre, True, theme.TEXT), (rect.x + 56, y + 8))
            for value, cx in (
                (unit.velocidad, cols[1][1]),
                (unit.ataque, cols[2][1]),
                (unit.defensa, cols[3][1]),
            ):
                surface.blit(self.font.render(str(value), True, theme.TEXT), (cx + 6, y + 8))

            surface.blit(
                self.small_font.render(self._best_terrain(unit), True, (150, 210, 150)),
                (cols[4][1], y + 6),
            )
            surface.blit(
                self.small_font.render(self._beats(game, unit), True, (230, 160, 120)),
                (cols[5][1], y + 6),
            )
            # Nota especial (arquero: ignora el bonus de fuerte).
            if unit.ignora_bonus_fort:
                surface.blit(
                    self.small_font.render(
                        "Ignora las murallas: ideal para asaltar fuertes.",
                        True, theme.TEXT_DIM,
                    ),
                    (rect.x + 56, y + 30),
                )
            y += 56

        tip = self.small_font.render(
            "Triángulo: soldado » caballero » arquero » soldado. La caballería"
            " odia bosque y montaña. Armá ejércitos mixtos.",
            True, ACCENT,
        )
        surface.blit(tip, (rect.x, y + 6))

    def _draw_map_and_sites(self, surface, game: Game, rect: pygame.Rect) -> int:
        y = self._section_title(surface, "El mapa", rect.x, rect.y)
        costs = game.config.get("costo_terreno", {})
        terrains = (
            Terrain.PLAINS, Terrain.FOREST, Terrain.FOREST_LIGHT,
            Terrain.MOUNTAIN, Terrain.MOUNTAIN_LIGHT, Terrain.MARSH, Terrain.WATER,
        )
        for terrain in terrains:
            sprite = self._terrain.get(terrain)
            if sprite is not None:
                surface.blit(sprite, (rect.x, y))
            name = TERRAIN_NAMES[terrain]
            if terrain is Terrain.WATER:
                detail = "intransitable (solo por puentes)"
            else:
                detail = f"costo de movimiento {costs.get(terrain.value, '?')}"
                extra = TERRAIN_DETAIL.get(terrain)
                if extra:
                    detail += f" — {extra}"
            surface.blit(self.font.render(name, True, theme.TEXT), (rect.x + 52, y + 4))
            surface.blit(
                self.small_font.render(detail, True, theme.TEXT_DIM), (rect.x + 200, y + 6)
            )
            y += TILE_SPRITE + 8

        y += 8
        y = self._section_title(surface, "Sitios", rect.x, y)
        for icon, name, lines in (
            ("fort", "Fuerte",
             "Produce tropas (reserva) y defiende fuerte (x1.5). Crea ejércitos desde él."),
            ("town", "Pueblo",
             "Da comida y reabastece. Defensa menor (x1.2). No produce tropas."),
        ):
            sprite = self._sites.get(icon)
            if sprite is not None:
                surface.blit(sprite, (rect.x, y))
            surface.blit(self.font.render(name, True, theme.TEXT), (rect.x + 52, y + 2))
            surface.blit(self.small_font.render(lines, True, theme.TEXT_DIM), (rect.x + 52, y + 26))
            y += TILE_SPRITE + 16
        return y

    def _draw_victory(self, surface, game: Game, rect: pygame.Rect) -> None:
        y = self._section_title(surface, "Cómo se gana", rect.x, rect.y)
        for mode, (name, desc) in VICTORY_LABELS.items():
            active = mode is game.victory_mode
            color = ACCENT if active else theme.TEXT_DIM
            if active:
                # Marcador dibujado (un triángulo): la fuente por defecto no
                # trae glyphs geométricos como ▶ y los muestra como un cuadro.
                pygame.draw.polygon(
                    surface, ACCENT,
                    [(rect.x, y + 4), (rect.x, y + 18), (rect.x + 12, y + 11)],
                )
            surface.blit(self.font.render(name, True, color), (rect.x + 22, y))
            surface.blit(self.small_font.render(desc, True, theme.TEXT_DIM), (rect.x + 150, y + 4))
            y += 32

    def _draw_start_here(self, surface, rect: pygame.Rect) -> None:
        y = self._section_title(surface, "Por dónde empezar", rect.x, rect.y)
        steps = [
            "1. Hacé clic en tu ejército inicial (los anillos amarillos lo señalan).",
            "2. Clic en un destino para trazar la ruta y apretá Fin del turno (Enter).",
            "3. Parate sobre un fuerte/pueblo neutral para capturarlo.",
            "4. Seleccioná el fuerte y usá «Crear ejército» para sacar tropas de la reserva.",
            "5. Mandá la tropa correcta al terreno correcto y presioná los fuertes del rival.",
            "6. Al chocar, elegí «Dirigir batalla» para pelear en tiempo real, o auto-resolver.",
        ]
        col_w = rect.width // 2
        for i, step in enumerate(steps):
            cx = rect.x + (col_w if i >= 3 else 0)
            cy = y + (i % 3) * 30
            surface.blit(self.font.render(step, True, theme.TEXT), (cx, cy))

    # --- helpers de datos ----------------------------------------------------

    def _best_terrain(self, unit) -> str:
        """Terrenos donde la clase tiene bonus (>1), por nombre legible."""
        good = [
            TERRAIN_NAMES.get(Terrain(t), t)
            for t, mult in sorted(unit.bonus_terreno.items(), key=lambda kv: -kv[1])
            if mult > 1.0
        ]
        return ", ".join(good) if good else "—"

    def _beats(self, game: Game, unit) -> str:
        """Clases contra las que tiene ventaja (bonus_vs > 1), por nombre."""
        names = [
            game.classes[c].nombre if c in game.classes else c
            for c, mult in unit.bonus_vs.items()
            if mult > 1.0
        ]
        return ", ".join(names) if names else "—"
