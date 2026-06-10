"""Pantalla de partida: input del jugador humano y orquestación del turno.

Interacción:
- Click izquierdo en un ejército propio: lo selecciona.
- Con un ejército seleccionado, click izquierdo en el mapa: traza el camino
  mínimo hasta ese tile (clicks sucesivos agregan waypoints).
- Sin ejército seleccionado, click en un fuerte propio: lo selecciona y el
  HUD ofrece "Crear ejército" (toma tropas de la reserva del fuerte).
- Click derecho: borra el camino trazado y deselecciona.
- Botón "Fin del turno" o Enter: ejecuta el turno (humano + AI).
"""

from __future__ import annotations

import pygame

from wom.ai.ai_player import AIPlayer
from wom.core.game import Game
from wom.core.orders import CreateArmyOrder, MoveOrder, Order
from wom.core.pathfind import shortest_path
from wom.core.victory import VictoryResult
from wom.core.worldmap import Coord
from wom.ui import theme
from wom.ui.assets import Assets
from wom.ui.hud import Hud
from wom.ui.renderer import MapRenderer


class GameScreen:
    def __init__(self, game: Game, human_id: int = 0, ai_level: str = "facil"):
        self.game = game
        self.human_id = human_id
        self.ais = [AIPlayer(p.id, ai_level) for p in game.players if p.is_ai]
        self.selected_id: int | None = None
        self.selected_fort: Coord | None = None
        self.pending_paths: dict[int, list[Coord]] = {}
        self.pending_creations: set[Coord] = set()
        self.result: VictoryResult | None = None

        window = pygame.display.get_surface().get_rect()
        map_area = pygame.Rect(
            0, 0, window.width - theme.SIDEBAR_WIDTH, window.height
        )
        tile_size = min(
            map_area.width // game.world.width, map_area.height // game.world.height
        )
        self.renderer = MapRenderer(game, Assets(tile_size), map_area)
        self.hud = Hud(
            pygame.Rect(map_area.right, 0, theme.SIDEBAR_WIDTH, window.height)
        )

    @property
    def game_over(self) -> bool:
        return self.result is not None and self.result.is_over

    # --- input -------------------------------------------------------------

    def handle_event(self, event: pygame.event.Event) -> None:
        if self.game_over:
            return
        if event.type == pygame.KEYDOWN and event.key == pygame.K_RETURN:
            self.end_turn()
        elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            if self.hud.hit_end_turn(event.pos):
                self.end_turn()
            elif self.selected_fort is not None and self.hud.hit_create_army(event.pos):
                self._toggle_creation(self.selected_fort)
            else:
                self._left_click(event.pos)
        elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 3:
            if self.selected_id is not None:
                self.pending_paths.pop(self.selected_id, None)
                self.selected_id = None
            self.selected_fort = None

    def _left_click(self, point: tuple[int, int]) -> None:
        tile = self.renderer.screen_to_tile(point, self.game)
        if tile is None:
            return
        occupant = self.game.army_at(tile)
        if occupant is not None and occupant.owner == self.human_id:
            self.selected_id = occupant.id
            self.selected_fort = None
            return
        if self.selected_id is not None:
            army = self.game.army_by_id(self.selected_id)
            if army is None:
                self.selected_id = None
                return
            self._extend_path(army.id, army.position, tile)
            return
        fort = self.game.world.fort_at(tile)
        if fort is not None and fort.owner == self.human_id:
            self.selected_fort = tile

    def _toggle_creation(self, fort_pos: Coord) -> None:
        """Encola (o desencola) la creación de un ejército en el fuerte."""
        if fort_pos in self.pending_creations:
            self.pending_creations.discard(fort_pos)
        else:
            fort = self.game.world.fort_at(fort_pos)
            if fort is not None and fort.reserve_total > 0:
                self.pending_creations.add(fort_pos)

    def _extend_path(self, army_id: int, army_pos: Coord, target: Coord) -> None:
        """Agrega al path pendiente el camino mínimo desde el último waypoint."""
        current = self.pending_paths.get(army_id, [])
        start = current[-1] if current else army_pos
        if target == start:
            return
        segment = shortest_path(
            self.game.world, start, target, self.game.config["costo_terreno"]
        )
        if segment:
            self.pending_paths[army_id] = current + segment

    # --- turno ---------------------------------------------------------------

    def end_turn(self) -> None:
        orders: list[Order] = [
            CreateArmyOrder(position=pos) for pos in self.pending_creations
        ]
        orders += [
            MoveOrder(army_id=army_id, path=tuple(path))
            for army_id, path in self.pending_paths.items()
            if path
        ]
        for ai in self.ais:
            orders.extend(ai.decide_orders(self.game))
        self.result = self.game.run_turn(orders)
        self.pending_paths.clear()
        self.pending_creations.clear()
        self.selected_id = None
        self.selected_fort = None

    # --- dibujo ----------------------------------------------------------------

    def draw(self, surface: pygame.Surface) -> None:
        surface.fill(theme.BACKGROUND)
        self.renderer.draw(
            surface, self.game, self.selected_id, self.pending_paths,
            self.selected_fort, self.pending_creations,
        )
        selected = (
            self.game.army_by_id(self.selected_id) if self.selected_id is not None else None
        )
        fort = (
            self.game.world.fort_at(self.selected_fort)
            if self.selected_fort is not None else None
        )
        self.hud.draw(
            surface, self.game, selected, fort,
            self.selected_fort in self.pending_creations, self.result,
        )
