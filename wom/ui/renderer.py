"""Dibujo del mapa: terreno, forts/towns, ejércitos, cruces, paths y selección."""

from __future__ import annotations

import pygame

from wom.core.army import Army
from wom.core.game import Game
from wom.core.worldmap import Coord, Terrain
from wom.ui import texture, theme
from wom.ui.animation import loop_frame
from wom.ui.assets import Assets, has_unit_animation, load_image, unit_frames
from wom.ui.camera import Camera
from wom.ui.pathline import arrow_head, smooth_path, trim_tail
from wom.ui.tiling import (
    WATERLIKE,
    dry_corners,
    dry_edges,
    ink_group,
    water_corners,
    water_tile,
)


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
        # Caches del estilo vintage (se rellenan perezosamente):
        #  - overlays de borde compuestos (terreno+lado+zoom),
        #  - papel y viñeta por tamaño de área.
        self._edge_overlays: dict[tuple[Terrain, str, int], pygame.Surface] = {}
        self._paper_cache: dict[tuple[int, int], pygame.Surface | None] = {}
        self._vignette_cache: dict[tuple[int, int], pygame.Surface] = {}
        self._compass_cache: dict[int, pygame.Surface] = {}
        self._ink_overlay_cache: dict[tuple[int, int], pygame.Surface] = {}
        self._paper_src = load_image("paper")
        self._compass_src = load_image("compass")
        # El terreno no cambia durante la partida: la variante de costa de
        # cada tile de agua, los bordes secos y los contornos de tinta se
        # calculan una sola vez (el editor los recalcula con refresh_terrain).
        self._world = world
        self.refresh_terrain()

    def refresh_terrain(self) -> None:
        """Recalcula la variante de costa de cada tile de agua (autotiling) y
        los overlays de esquina. Cubre agua y puentes por igual: el puente se
        dibuja como agua autotileada con el tablón (transparente) encima, así
        la orilla sigue por debajo sin cortes.

        En la partida el terreno es fijo y esto corre una vez; el editor lo
        invoca tras cada pincelada para que la costa se re-autotile en vivo.
        """
        world = self._world
        self._water_tiles = {}
        self._water_corners = {}
        self._dry_edges: dict[Coord, list[tuple[str, Terrain]]] = {}
        for y in range(world.height):
            for x in range(world.width):
                if world.tiles[y][x] in WATERLIKE:
                    self._water_tiles[(x, y)] = water_tile(world, (x, y))
                    corners = water_corners(world, (x, y))
                    if corners:
                        self._water_corners[(x, y)] = corners
                    continue
                # Terreno seco: bordes (rectos + diagonales) hacia el terreno
                # vecino dominante, para fundirlos sin cortar en cuadrado.
                edges = dry_edges(world, (x, y)) + dry_corners(world, (x, y))
                if edges:
                    self._dry_edges[(x, y)] = edges
        self._ink_segments = self._compute_ink_segments(world)

    def _compute_ink_segments(self, world) -> list[list[tuple[float, float]]]:
        """Segmentos de contorno (en coords de tile) entre grupos cartográficos
        distintos: costas, lindes de bosque, pie de montaña, borde de pantano.
        Cada borde compartido se quiebra en un punto medio con jitter
        determinista para que la línea parezca dibujada a mano."""
        segments: list[list[tuple[float, float]]] = []
        for y in range(world.height):
            for x in range(world.width):
                group = ink_group(world.tiles[y][x])
                if x + 1 < world.width and ink_group(world.tiles[y][x + 1]) != group:
                    segments.append(self._ink_edge((x + 1, y), (x + 1, y + 1), axis=0))
                if y + 1 < world.height and ink_group(world.tiles[y + 1][x]) != group:
                    segments.append(self._ink_edge((x, y + 1), (x + 1, y + 1), axis=1))
        return segments

    @staticmethod
    def _ink_edge(p0: Coord, p1: Coord, axis: int) -> list[tuple[float, float]]:
        """Polilínea de 3 puntos para un borde compartido, con el medio corrido
        perpendicular (jitter por hash → trazo ondulado, no recto)."""
        frac = (texture.tile_hash(p0[0], p0[1], salt=5) % 1000) / 999.0
        offset = (frac - 0.5) * 0.24  # amplitud del meandro, en tiles
        mx = (p0[0] + p1[0]) / 2
        my = (p0[1] + p1[1]) / 2
        if axis == 0:  # borde vertical: corre en x
            mid = (mx + offset, my)
        else:  # borde horizontal: corre en y
            mid = (mx, my + offset)
        return [(float(p0[0]), float(p0[1])), mid, (float(p1[0]), float(p1[1]))]

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

    def _tile_point(self, tx: float, ty: float) -> tuple[int, int]:
        """Punto de pantalla de una coordenada de tile fraccionaria (esquinas
        de tile, para los contornos de tinta)."""
        ts = self.tile_size
        return (round(self.origin[0] + tx * ts), round(self.origin[1] + ty * ts))

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
        self._draw_vintage(surface, game)
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
                rect = self.tile_rect((x, y))
                terrain = game.world.tiles[y][x]
                variant = self._water_tiles.get((x, y))
                if variant is None:  # terreno seco (pradera/bosque/montaña)
                    styles = assets.terrain_styles[terrain]
                    idx = texture.style_index(x, y, len(styles))
                    surface.blit(styles[idx], rect)
                    for side, neighbor in self._dry_edges.get((x, y), ()):
                        surface.blit(self._edge_overlay(neighbor, side), rect)
                    continue
                surface.blit(assets.water[variant], rect)  # agua autotileada
                for corner in self._water_corners.get((x, y), ()):
                    surface.blit(assets.water_corners[corner], rect)
                if terrain is Terrain.BRIDGE_H or terrain is Terrain.BRIDGE_V:
                    surface.blit(assets.terrain[terrain], rect)  # tablón encima

    def _edge_overlay(self, neighbor: Terrain, side: str) -> pygame.Surface:
        """Textura del terreno vecino recortada por la máscara del lado `side`
        (banda recta o esquina). Cacheado por (terreno, lado, zoom)."""
        key = (neighbor, side, self.tile_size)
        cached = self._edge_overlays.get(key)
        if cached is None:
            assets = self.assets
            ts = self.tile_size
            # Sobre una superficie con alfa: la textura (opaca) del vecino y
            # encima la máscara, que recorta el alfa a la banda del borde.
            comp = pygame.Surface((ts, ts), pygame.SRCALPHA)
            comp.blit(assets.terrain[neighbor], (0, 0))
            comp.blit(assets.edge_masks[side], (0, 0), special_flags=pygame.BLEND_RGBA_MULT)
            self._edge_overlays[key] = comp
            cached = comp
        return cached

    def _draw_vintage(self, surface: pygame.Surface, game: Game) -> None:
        """Capa de estilo "pergamino": contornos de tinta sobre los límites de
        terreno + textura de papel + viñeta. Se pinta sobre el terreno y antes
        de sitios/ejércitos, para que esos queden nítidos por encima."""
        self._draw_ink_contours(surface)
        ts = self.tile_size
        world = self._world
        map_rect = pygame.Rect(
            self.origin[0], self.origin[1], world.width * ts, world.height * ts
        ).clip(self.area)
        if map_rect.width <= 0 or map_rect.height <= 0:
            return
        prev_clip = surface.get_clip()
        surface.set_clip(map_rect)  # papel/viñeta solo sobre el mapa, no el margen
        paper = self._paper_overlay()
        if paper is not None:
            surface.blit(paper, self.area.topleft)
        surface.blit(self._vignette(), self.area.topleft)
        self._draw_chrome(surface, map_rect)
        surface.set_clip(prev_clip)

    def _draw_chrome(self, surface: pygame.Surface, map_rect: pygame.Rect) -> None:
        """Adornos cartográficos sobre el mapa: marco de tinta y rosa de los
        vientos (ambos reemplazables por arte propio vía theme/compass.png)."""
        if theme.MAP_FRAME:
            inset = max(3, self.tile_size // 12)
            pygame.draw.rect(surface, theme.INK, map_rect.inflate(-2 * inset, -2 * inset), 2)
            pygame.draw.rect(
                surface, theme.INK, map_rect.inflate(-2 * inset - 6, -2 * inset - 6), 1
            )
        if self._compass_src is not None:
            size = int(min(map_rect.width, map_rect.height) * theme.COMPASS_SIZE_FRAC)
            size = max(48, min(size, 220))
            compass = self._compass_cache.get(size)
            if compass is None:
                compass = pygame.transform.smoothscale(self._compass_src, (size, size))
                compass.fill((255, 255, 255, 210), special_flags=pygame.BLEND_RGBA_MULT)
                self._compass_cache[size] = compass
            margin = max(6, size // 6)
            pos = (map_rect.right - size - margin, map_rect.bottom - size - margin)
            surface.blit(compass, pos)

    def _draw_ink_contours(self, surface: pygame.Surface) -> None:
        alpha = int(255 * theme.INK_STRENGTH)
        if alpha <= 0 or not self._ink_segments:  # contornos apagados
            return
        width = max(1, self.tile_size // 32)
        if alpha >= 255:  # tinta plena: dibujar directo (sin overlay)
            for seg in self._ink_segments:
                points = [self._tile_point(tx, ty) for tx, ty in seg]
                pygame.draw.lines(surface, theme.INK, False, points, width)
            return
        # Opacidad parcial: dibujar sobre un overlay con alfa y blittearlo una
        # vez (los segmentos comparten vértices; pygame.draw sobreescribe, así
        # que los cruces no se oscurecen al duplicarse).
        ox, oy = self.area.topleft
        overlay = self._ink_overlay()
        color = (*theme.INK, alpha)
        for seg in self._ink_segments:
            points = [
                (round(self.origin[0] + tx * self.tile_size - ox),
                 round(self.origin[1] + ty * self.tile_size - oy))
                for tx, ty in seg
            ]
            pygame.draw.lines(overlay, color, False, points, width)
        surface.blit(overlay, self.area.topleft)

    def _ink_overlay(self) -> pygame.Surface:
        """Overlay transparente del tamaño del área para los contornos con
        opacidad parcial. Se reutiliza (limpiándolo) entre frames."""
        key = (self.area.width, self.area.height)
        surf = self._ink_overlay_cache.get(key)
        if surf is None:
            surf = pygame.Surface((self.area.width, self.area.height), pygame.SRCALPHA)
            self._ink_overlay_cache[key] = surf
        else:
            surf.fill((0, 0, 0, 0))
        return surf

    def _paper_overlay(self) -> pygame.Surface | None:
        """Textura de papel escalada al área, con su intensidad ya aplicada
        (alfa × PAPER_STRENGTH). Cacheada por tamaño de área."""
        if self._paper_src is None or theme.PAPER_STRENGTH <= 0:
            return None
        key = (self.area.width, self.area.height)
        cached = self._paper_cache.get(key)
        if cached is None:
            scaled = pygame.transform.smoothscale(
                self._paper_src, (self.area.width, self.area.height)
            ).convert_alpha()
            alpha = int(255 * theme.PAPER_STRENGTH)
            scaled.fill((255, 255, 255, alpha), special_flags=pygame.BLEND_RGBA_MULT)
            self._paper_cache[key] = scaled
            cached = scaled
        return cached

    def _vignette(self) -> pygame.Surface:
        """Oscurecimiento cálido de los bordes (scroll quemado), cacheado por
        tamaño de área. Anillos de borde con alfa creciente hacia el margen."""
        key = (self.area.width, self.area.height)
        cached = self._vignette_cache.get(key)
        if cached is not None:
            return cached
        w, h = self.area.width, self.area.height
        surf = pygame.Surface((w, h), pygame.SRCALPHA)
        margin = max(8, min(w, h) // 5)
        max_alpha = int(255 * theme.VIGNETTE_STRENGTH)
        for i in range(margin):
            alpha = int(max_alpha * (1 - i / margin) ** 2)
            if alpha <= 0:
                continue
            pygame.draw.rect(surf, (28, 20, 12, alpha), (i, i, w - 2 * i, h - 2 * i), 1)
        self._vignette_cache[key] = surf
        return surf

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
        *,
        moving: bool = False,
        anim_time: float = 0.0,
        anim_seed: int = 0,
    ) -> pygame.Rect:
        """Dibuja un ejército en una posición de tile, entera o fraccionaria
        (la animación de movimiento interpola entre tiles).

        Si la clase dominante tiene animación, usa la **pose** quieto y el
        **ciclo de caminata** mientras se mueve (`moving`, durante el recap de
        fin de turno); `anim_seed` desfasa el ciclo por ejército."""
        ts = self.tile_size
        rect = pygame.Rect(
            round(self.origin[0] + tile_pos[0] * ts),
            round(self.origin[1] + tile_pos[1] * ts),
            ts, ts,
        )
        sprite = self._army_sprite(class_id, moving, anim_time, anim_seed)
        surface.blit(sprite, sprite.get_rect(center=rect.center))
        pygame.draw.rect(surface, theme.player_color(owner), rect, 2)
        count = self.count_font.render(str(troops), True, theme.TEXT)
        shadow = self.count_font.render(str(troops), True, (0, 0, 0))
        pos = (rect.right - count.get_width() - 2, rect.bottom - count.get_height())
        surface.blit(shadow, (pos[0] + 1, pos[1] + 1))
        surface.blit(count, pos)
        return rect

    def _army_sprite(
        self, class_id: str, moving: bool, anim_time: float, anim_seed: int
    ) -> pygame.Surface:
        """Sprite del ejército: pose/caminata si la clase anima, si no el estático."""
        if has_unit_animation(class_id):
            state = "walk" if moving else "idle"
            frames = unit_frames(class_id, state, self.assets.unit_size)
            if frames:
                if state == "walk":
                    idx = loop_frame(anim_time, len(frames), phase=(anim_seed % 7) * 0.13)
                else:
                    idx = 0
                return frames[idx]
        return self.assets.units[class_id]
