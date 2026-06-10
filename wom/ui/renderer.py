"""Dibujo del mapa: terreno, forts/towns, ejércitos, cruces, paths y selección."""

from __future__ import annotations

import pygame

from wom.core.army import Army
from wom.core.game import Game
from wom.core.worldmap import Coord
from wom.ui import theme
from wom.ui.assets import Assets


class MapRenderer:
    """Convierte coordenadas de tile a pantalla y dibuja el estado del juego."""

    def __init__(self, game: Game, assets: Assets, area: pygame.Rect):
        self.assets = assets
        self.tile_size = assets.tile_size
        world = game.world
        self.origin = (
            area.x + (area.width - world.width * self.tile_size) // 2,
            area.y + (area.height - world.height * self.tile_size) // 2,
        )
        self.count_font = pygame.font.SysFont(None, max(14, int(self.tile_size * 0.55)))

    # --- coordenadas ------------------------------------------------------

    def tile_rect(self, pos: Coord) -> pygame.Rect:
        x, y = pos
        ts = self.tile_size
        return pygame.Rect(self.origin[0] + x * ts, self.origin[1] + y * ts, ts, ts)

    def tile_center(self, pos: Coord) -> tuple[int, int]:
        return self.tile_rect(pos).center

    def screen_to_tile(self, point: tuple[int, int], game: Game) -> Coord | None:
        ts = self.tile_size
        x = (point[0] - self.origin[0]) // ts
        y = (point[1] - self.origin[1]) // ts
        return (x, y) if game.world.in_bounds((x, y)) else None

    # --- dibujo -------------------------------------------------------------

    def draw(
        self,
        surface: pygame.Surface,
        game: Game,
        selected_id: int | None,
        pending_paths: dict[int, list[Coord]],
        selected_fort: Coord | None = None,
        pending_creations: set[Coord] = frozenset(),
        hide_armies: bool = False,
    ) -> None:
        """Dibuja el estado del juego. Con `hide_armies` omite los ejércitos:
        durante la animación de fin de turno los dibuja GameScreen en sus
        posiciones interpoladas (draw_army_at)."""
        self._draw_terrain(surface, game)
        self._draw_sites(surface, game)
        self._draw_crosses(surface, game)
        self._draw_paths(surface, game, selected_id, pending_paths)
        if not hide_armies:
            for army in game.armies:
                self._draw_army(surface, game, army, selected=army.id == selected_id)
        if selected_fort is not None:
            pygame.draw.rect(surface, theme.SELECTION, self.tile_rect(selected_fort), 3)
        for pos in pending_creations:
            marker = self.count_font.render("+", True, theme.SELECTION)
            rect = self.tile_rect(pos)
            surface.blit(marker, (rect.x + 3, rect.bottom - marker.get_height()))

    def _draw_terrain(self, surface: pygame.Surface, game: Game) -> None:
        for y in range(game.world.height):
            for x in range(game.world.width):
                tile = self.assets.terrain[game.world.tiles[y][x]]
                surface.blit(tile, self.tile_rect((x, y)))

    def _draw_sites(self, surface: pygame.Surface, game: Game) -> None:
        for kind, sites in (("fort", game.world.forts), ("town", game.world.towns)):
            icon = self.assets.icons[kind]
            flag = self.assets.icons["flag"]
            for site in sites:
                rect = self.tile_rect(site.position)
                surface.blit(icon, icon.get_rect(center=rect.center))
                pygame.draw.rect(surface, theme.player_color(site.owner), rect, 3)
                if site.has_flag:
                    surface.blit(flag, (rect.x + 2, rect.y + 2))
                if kind == "fort" and site.reserve_total > 0:
                    text = self.count_font.render(str(site.reserve_total), True, theme.TEXT)
                    shadow = self.count_font.render(str(site.reserve_total), True, (0, 0, 0))
                    pos = (rect.right - text.get_width() - 2, rect.y + 1)
                    surface.blit(shadow, (pos[0] + 1, pos[1] + 1))
                    surface.blit(text, pos)

    def _draw_crosses(self, surface: pygame.Surface, game: Game) -> None:
        cross = self.assets.icons["cross"]
        for pos in game.crosses:
            surface.blit(cross, cross.get_rect(center=self.tile_center(pos)))

    def _draw_paths(
        self,
        surface: pygame.Surface,
        game: Game,
        selected_id: int | None,
        pending_paths: dict[int, list[Coord]],
    ) -> None:
        # Órdenes de turnos anteriores todavía en curso.
        for army in game.armies:
            if army.path and army.id not in pending_paths:
                self._draw_path(surface, army.position, army.path, theme.PATH_ONGOING)
        # Paths nuevos trazados este turno.
        for army_id, path in pending_paths.items():
            army = game.army_by_id(army_id)
            if army is None or not path:
                continue
            color = theme.PATH_PENDING if army_id == selected_id else theme.PATH_OTHERS
            self._draw_path(surface, army.position, path, color)

    def _draw_path(
        self, surface: pygame.Surface, start: Coord, path: list[Coord], color
    ) -> None:
        points = [self.tile_center(start)] + [self.tile_center(p) for p in path]
        pygame.draw.lines(surface, color, False, points, 2)
        pygame.draw.circle(surface, color, points[-1], max(3, self.tile_size // 8))

    def _draw_army(
        self, surface: pygame.Surface, game: Game, army: Army, *, selected: bool
    ) -> None:
        dominant = max(army.composition, key=lambda c: army.composition[c])
        rect = self.draw_army_at(
            surface, army.owner, dominant, army.total_troops, army.position
        )
        if selected:
            pygame.draw.rect(surface, theme.SELECTION, rect, 3)

    def draw_army_at(
        self,
        surface: pygame.Surface,
        owner: int,
        class_id: str,
        troops: int,
        tile_pos: tuple[float, float],
    ) -> pygame.Rect:
        """Dibuja un ejército en una posición de tile, entera o fraccionaria
        (la animación de movimiento interpola entre tiles)."""
        ts = self.tile_size
        rect = pygame.Rect(
            round(self.origin[0] + tile_pos[0] * ts),
            round(self.origin[1] + tile_pos[1] * ts),
            ts, ts,
        )
        sprite = self.assets.units[class_id]
        surface.blit(sprite, sprite.get_rect(center=rect.center))
        pygame.draw.rect(surface, theme.player_color(owner), rect, 2)
        count = self.count_font.render(str(troops), True, theme.TEXT)
        shadow = self.count_font.render(str(troops), True, (0, 0, 0))
        pos = (rect.right - count.get_width() - 2, rect.bottom - count.get_height())
        surface.blit(shadow, (pos[0] + 1, pos[1] + 1))
        surface.blit(count, pos)
        return rect
