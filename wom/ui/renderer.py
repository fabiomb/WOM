"""Dibujo del mapa: terreno, forts/towns, ejércitos, cruces, paths y selección."""

from __future__ import annotations

import pygame

from wom.core.army import Army
from wom.core.game import Game
from wom.core.worldmap import Coord, Terrain
from wom.ui import theme
from wom.ui.assets import Assets
from wom.ui.camera import Camera
from wom.ui.pathline import arrow_head, smooth_path, trim_tail
from wom.ui.tiling import water_tile


class MapRenderer:
    """Convierte coordenadas de tile a pantalla y dibuja el estado del juego.

    La cámara (zoom con la rueda + paneo por bordes) decide tile_size y
    origin; los assets y la fuente de contadores se cachean por nivel de
    zoom (se escalan una sola vez la primera vez que se usa cada nivel).
    """

    def __init__(self, game: Game, assets: Assets, area: pygame.Rect):
        self.area = area
        world = game.world
        self.camera = Camera(
            (area.x, area.y, area.width, area.height),
            (world.width, world.height),
            assets.tile_size,
        )
        self._assets_by_size: dict[int, Assets] = {assets.tile_size: assets}
        self._fonts_by_size: dict[int, pygame.font.Font] = {}
        # El terreno no cambia durante la partida: la variante de costa de
        # cada tile de agua se calcula una sola vez (el editor la recalcula a
        # mano con refresh_terrain cuando pinta).
        self._world = world
        self.refresh_terrain()

    def refresh_terrain(self) -> None:
        """Recalcula la variante de costa de cada tile de agua (autotiling).

        En la partida el terreno es fijo y esto corre una vez; el editor lo
        invoca tras cada pincelada para que la costa se re-autotile en vivo.
        """
        world = self._world
        self._water_tiles = {
            (x, y): water_tile(world, (x, y))
            for y in range(world.height)
            for x in range(world.width)
            if world.tiles[y][x] is Terrain.WATER
        }

    @property
    def tile_size(self) -> int:
        return self.camera.tile_size

    @property
    def origin(self) -> tuple[int, int]:
        return self.camera.origin

    @property
    def assets(self) -> Assets:
        size = self.tile_size
        if size not in self._assets_by_size:
            self._assets_by_size[size] = Assets(size)
        return self._assets_by_size[size]

    @property
    def count_font(self) -> pygame.font.Font:
        size = self.tile_size
        if size not in self._fonts_by_size:
            self._fonts_by_size[size] = pygame.font.SysFont(
                None, max(14, int(size * 0.55))
            )
        return self._fonts_by_size[size]

    def zoom(self, direction: int, anchor: tuple[int, int]) -> bool:
        """Zoom con la rueda, anclado a la posición del mouse en el canvas."""
        if not self.area.collidepoint(anchor):
            return False
        return self.camera.zoom(direction, anchor)

    # --- coordenadas ------------------------------------------------------

    def tile_rect(self, pos: Coord) -> pygame.Rect:
        x, y = pos
        ts = self.tile_size
        return pygame.Rect(self.origin[0] + x * ts, self.origin[1] + y * ts, ts, ts)

    def tile_center(self, pos: Coord) -> tuple[int, int]:
        return self.tile_rect(pos).center

    def screen_to_tile(self, point: tuple[int, int], game: Game) -> Coord | None:
        if not self.area.collidepoint(point):
            return None  # con zoom, un punto del HUD podría mapear a un tile
        ts = self.tile_size
        x = (point[0] - self.origin[0]) // ts
        y = (point[1] - self.origin[1]) // ts
        return (x, y) if game.world.in_bounds((x, y)) else None

    def visible_tiles(self, game: Game) -> tuple[range, range]:
        """Rangos de tiles que caen dentro del área visible (con zoom el
        mapa excede el área y no tiene sentido dibujar el resto)."""
        ts = self.tile_size
        ox, oy = self.origin
        x0 = max(0, (self.area.left - ox) // ts)
        y0 = max(0, (self.area.top - oy) // ts)
        x1 = min(game.world.width, (self.area.right - ox) // ts + 1)
        y1 = min(game.world.height, (self.area.bottom - oy) // ts + 1)
        return range(x0, x1), range(y0, y1)

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
        hide_new_crosses: bool = False,
    ) -> None:
        """Dibuja el estado del juego. Con `hide_armies` omite los ejércitos:
        durante la animación de fin de turno los dibuja GameScreen en sus
        posiciones interpoladas (draw_army_at). Con `hide_new_crosses` omite
        las cruces del turno recién jugado: la animación de batalla todavía
        no reveló esas muertes."""
        self._draw_terrain(surface, game)
        self._draw_sites(surface, game)
        self._draw_crosses(surface, game, hide_new=hide_new_crosses)
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
        assets = self.assets  # una sola resolución del nivel de zoom
        xs, ys = self.visible_tiles(game)
        for y in ys:
            for x in xs:
                variant = self._water_tiles.get((x, y))
                if variant is not None:
                    tile = assets.water[variant]
                else:
                    tile = assets.terrain[game.world.tiles[y][x]]
                surface.blit(tile, self.tile_rect((x, y)))

    def _draw_sites(self, surface: pygame.Surface, game: Game) -> None:
        for kind, sites in (("fort", game.world.forts), ("town", game.world.towns)):
            icon = self.assets.icons[kind]
            for site in sites:
                rect = self.tile_rect(site.position)
                surface.blit(icon, icon.get_rect(center=rect.center))
                pygame.draw.rect(surface, theme.player_color(site.owner), rect, 3)
                if site.has_flag:
                    flag = self.assets.icons[theme.flag_icon(site.owner)]
                    surface.blit(flag, (rect.x + 2, rect.y + 2))
                if kind == "fort" and site.reserve_total > 0:
                    text = self.count_font.render(str(site.reserve_total), True, theme.TEXT)
                    shadow = self.count_font.render(str(site.reserve_total), True, (0, 0, 0))
                    pos = (rect.right - text.get_width() - 2, rect.y + 1)
                    surface.blit(shadow, (pos[0] + 1, pos[1] + 1))
                    surface.blit(text, pos)

    def _draw_crosses(
        self, surface: pygame.Surface, game: Game, hide_new: bool = False
    ) -> None:
        """Las cruces se desvanecen con la edad hasta que el core las purga
        (turnos_cruz): opacidad plena el turno siguiente a la muerte y un
        quinto por turno desde ahí. Con `hide_new` se omiten las del último
        run_turn (died_turn == turn - 1)."""
        cross = self.assets.icons["cross"]
        lifetime = game.config["turnos_cruz"]
        for x, y, died_turn in game.crosses:
            if hide_new and died_turn >= game.turn - 1:
                continue
            age = game.turn - died_turn
            opacity = max(0.0, min(1.0, (lifetime - age + 1) / lifetime))
            cross.set_alpha(round(255 * opacity))
            surface.blit(cross, cross.get_rect(center=self.tile_center((x, y))))
        cross.set_alpha(255)

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
        """Trazo "a mano alzada" sobre el mapa: curva suave (esquinas
        redondeadas) rematada con una punta de flecha."""
        points = [self.tile_center(start)] + [self.tile_center(p) for p in path]
        curve = smooth_path(points)
        head = arrow_head(curve, max(6.0, self.tile_size * 0.4))
        if head:
            # el trazo termina en la base de la flecha, no debajo de ella
            curve = trim_tail(curve, max(6.0, self.tile_size * 0.4) * 0.7)
        width = max(2, self.tile_size // 14)
        if len(curve) >= 2:
            pygame.draw.lines(surface, color, False, curve, width)
        if head:
            pygame.draw.polygon(surface, color, head)

    def _draw_army(
        self, surface: pygame.Surface, game: Game, army: Army, *, selected: bool
    ) -> None:
        dominant = max(army.composition, key=lambda c: army.composition[c])
        rect = self.draw_army_at(
            surface, army.owner, dominant, army.total_troops, army.position
        )
        if selected:
            pygame.draw.rect(surface, theme.SELECTION, rect, 3)

    def draw_tile_rings(
        self,
        surface: pygame.Surface,
        pos: Coord,
        rings: list[tuple[float, float]],
        color: tuple[int, int, int],
    ) -> None:
        """Anillos esfumados alrededor de un tile (señala el ejército inicial).

        `rings` son pares (radio en tiles, opacidad 0..1), como los devuelve
        animation.spawn_highlight_rings.
        """
        if not rings:
            return
        ts = self.tile_size
        width = max(2, ts // 16)
        extent = int(max(radius for radius, _ in rings) * ts) + width + 1
        overlay = pygame.Surface((2 * extent, 2 * extent), pygame.SRCALPHA)
        for radius_tiles, opacity in rings:
            pygame.draw.circle(
                overlay, (*color, round(255 * opacity)),
                (extent, extent), round(radius_tiles * ts), width,
            )
        surface.blit(overlay, overlay.get_rect(center=self.tile_center(pos)))

    def draw_clash(
        self, surface: pygame.Surface, tile_pos: tuple[float, float], intensity: float
    ) -> None:
        """Destello en el punto de contacto de una batalla (fase de choque).

        `tile_pos` admite coordenadas fraccionarias (el punto medio entre
        ambos bandos); `intensity` 0..1 modula tamaño y opacidad.
        """
        ts = self.tile_size
        center = (
            round(self.origin[0] + (tile_pos[0] + 0.5) * ts),
            round(self.origin[1] + (tile_pos[1] + 0.5) * ts),
        )
        radius = max(2, int(ts * (0.25 + 0.35 * intensity)))
        overlay = pygame.Surface((radius * 2, radius * 2), pygame.SRCALPHA)
        alpha = round(90 + 130 * intensity)
        pygame.draw.circle(
            overlay, (*theme.CLASH_FLASH, alpha), (radius, radius), radius
        )
        pygame.draw.circle(
            overlay, (*theme.CLASH_CORE, min(255, alpha + 60)),
            (radius, radius), max(2, radius // 2),
        )
        surface.blit(overlay, overlay.get_rect(center=center))

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
