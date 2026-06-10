"""Pantalla de partida: input del jugador humano y orquestación del turno.

Interacción:
- Click izquierdo en un ejército propio: lo selecciona.
- Con un ejército seleccionado, click izquierdo en el mapa: traza el camino
  mínimo hasta ese tile (clicks sucesivos agregan waypoints).
- Sin ejército seleccionado, click en un fuerte propio: lo selecciona y el
  HUD ofrece "Crear ejército" (toma tropas de la reserva del fuerte).
- Click derecho: borra el camino trazado y deselecciona.
- Botón "Fin del turno" o Enter: ejecuta el turno (humano + AI). El
  movimiento resultante se anima de forma fluida (Enter/Espacio/click la
  saltea); el resto del input queda bloqueado mientras tanto.
- Botón "Guardar" o tecla G: guarda la partida en saves/ con timestamp.
- ESC: vuelve al menú principal (la partida no guardada se pierde).
"""

from __future__ import annotations

import pygame

from wom.ai.ai_player import AIPlayer
from wom.core.game import Game
from wom.core.orders import CreateArmyOrder, MoveOrder, Order
from wom.core.pathfind import shortest_path
from wom.core.victory import VictoryResult
from wom.core.worldmap import Coord
from wom.persistence.savegame import save_game
from wom.ui import theme
from wom.ui.animation import TurnAnimation, build_turn_animation
from wom.ui.assets import Assets
from wom.ui.hud import Hud
from wom.ui.renderer import MapRenderer


class GameScreen:
    def __init__(self, game: Game, human_id: int = 0, ai_level: str = "facil"):
        self.game = game
        self.human_id = human_id
        # El nivel viaja en Player.ai_level (savegames); el parámetro es fallback.
        self.ais = [
            AIPlayer(p.id, p.ai_level or ai_level) for p in game.players if p.is_ai
        ]
        self.selected_id: int | None = None
        self.selected_fort: Coord | None = None
        self.pending_paths: dict[int, list[Coord]] = {}
        self.pending_creations: set[Coord] = set()
        self.result: VictoryResult | None = None
        self.wants_menu = False  # lo activa ESC; lo consume el loop de app
        self.notice: str | None = None  # aviso temporal del HUD ("guardada...")
        self.notice_until = 0
        self.animation: TurnAnimation | None = None
        self.animation_start = 0  # ticks de pygame al iniciar la animación

        window = pygame.display.get_surface().get_rect()
        map_area = pygame.Rect(
            0, 0, window.width - theme.SIDEBAR_WIDTH, window.height
        )
        tile_size = min(
            map_area.width // game.world.width, map_area.height // game.world.height
        )
        self.renderer = MapRenderer(game, Assets(tile_size), map_area)
        self.hud = Hud(
            pygame.Rect(map_area.right, 0, theme.SIDEBAR_WIDTH, window.height),
            human_id=human_id,
        )

    @property
    def game_over(self) -> bool:
        return self.result is not None and self.result.is_over

    @property
    def animating(self) -> bool:
        return self.animation is not None and not self.animation.finished(
            self._animation_elapsed()
        )

    def _animation_elapsed(self) -> float:
        return (pygame.time.get_ticks() - self.animation_start) / 1000.0

    # --- input -------------------------------------------------------------

    def handle_event(self, event: pygame.event.Event) -> None:
        if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
            self.wants_menu = True
            return
        if self.animating:
            # Mientras anima solo se acepta saltearla; el estado ya es el final.
            if (
                event.type == pygame.MOUSEBUTTONDOWN
                or (event.type == pygame.KEYDOWN
                    and event.key in (pygame.K_RETURN, pygame.K_SPACE))
            ):
                self.animation.skip()
            return
        if self.game_over:
            return
        if event.type == pygame.KEYDOWN and event.key == pygame.K_RETURN:
            self.end_turn()
        elif event.type == pygame.KEYDOWN and event.key == pygame.K_g:
            self.save()
        elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            if self.hud.hit_end_turn(event.pos):
                self.end_turn()
            elif self.hud.hit_save(event.pos):
                self.save()
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

    # --- guardado --------------------------------------------------------------

    def save(self) -> None:
        """Guarda la partida con timestamp y muestra un aviso unos segundos."""
        path = save_game(self.game)
        self.notice = f"Partida guardada: {path.name}"
        self.notice_until = pygame.time.get_ticks() + 3000

    # --- turno ---------------------------------------------------------------

    def end_turn(self) -> None:
        self.animation = None  # descarta una animación anterior si la hubiera
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
        # Snapshot pre-turno: conserva sprite/posición de los que mueran.
        pre_turn = [army.to_dict() for army in self.game.armies]
        self.result = self.game.run_turn(orders)
        self.animation = build_turn_animation(self.game, pre_turn)
        self.animation_start = pygame.time.get_ticks()
        self.pending_paths.clear()
        self.pending_creations.clear()
        self.selected_id = None
        self.selected_fort = None

    # --- dibujo ----------------------------------------------------------------

    def draw(self, surface: pygame.Surface) -> None:
        surface.fill(theme.BACKGROUND)
        animating = self.animating  # una sola lectura del reloj por frame
        self.renderer.draw(
            surface, self.game, self.selected_id, self.pending_paths,
            self.selected_fort, self.pending_creations, hide_armies=animating,
        )
        if animating:
            for motion, pos in self.animation.positions(self._animation_elapsed()):
                self.renderer.draw_army_at(
                    surface, motion.owner, motion.class_id, motion.troops, pos
                )
        elif self.animation is not None:
            self.animation = None  # terminó: vuelve el dibujo normal
        selected = (
            self.game.army_by_id(self.selected_id) if self.selected_id is not None else None
        )
        fort = (
            self.game.world.fort_at(self.selected_fort)
            if self.selected_fort is not None else None
        )
        notice = self.notice if pygame.time.get_ticks() < self.notice_until else None
        # El overlay de fin de partida espera a que termine la animación
        # del último turno (que se vea el movimiento que la definió).
        result = None if animating else self.result
        self.hud.draw(
            surface, self.game, selected, fort,
            self.selected_fort in self.pending_creations, result, notice,
        )
