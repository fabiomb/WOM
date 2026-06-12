"""Pantalla de partida: input del jugador humano y orquestación del turno.

Interacción:
- Click izquierdo en un ejército propio: lo selecciona.
- Con un ejército seleccionado, click izquierdo en el mapa: traza el camino
  mínimo hasta ese tile (clicks sucesivos agregan waypoints). Otro click en
  el mismo ejército (o ESC) confirma la ruta y deselecciona; doble click en
  el destino hace lo mismo en un solo gesto (fija la ruta y libera el foco).
- Rueda del mouse: zoom in/out anclado al cursor; con zoom, llevar el mouse
  a los bordes del área de mapa panea la vista (ver wom/ui/camera.py).
- Shift+click en un ejército propio: lo elige para fusionar; shift+click en
  otro propio aledaño abre el modal de transferencia, donde se elige "Todo"
  o la cantidad por clase que pasa al destino (Game.transfer_troops).
- Con un ejército seleccionado, botón "Dividir ejército" o tecla D: modal
  para elegir qué tropas por clase forman un ejército nuevo en un tile
  aledaño libre (Game.split_army).
- Sin ejército seleccionado, click en un fuerte propio: lo selecciona y el
  HUD ofrece "Crear ejército" (toma tropas de la reserva del fuerte).
- Click derecho: borra el camino trazado y deselecciona.
- Botón "Fin del turno" o Enter: ejecuta el turno (humano + AI). El
  movimiento resultante se anima de forma fluida y las batallas muestran
  ambos bandos chocando con un destello (Enter/Espacio/click saltea la
  animación); el resto del input queda bloqueado mientras tanto.

Al empezar una partida nueva, anillos esfumados señalan durante unos
segundos dónde está el ejército inicial del jugador (se apagan solos, al
seleccionarlo o al terminar el primer turno).
- Botón "Guardar" o tecla G: guarda la partida en saves/ con timestamp.
- ESC: pide confirmación antes de volver al menú (la partida no guardada se
  pierde); con la partida ya terminada sale directo.
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
from wom.ui import scale, theme
from wom.ui.animation import (
    TurnAnimation,
    build_turn_animation,
    spawn_highlight_rings,
)
from wom.ui.assets import Assets
from wom.ui.dialogs import TroopPicker
from wom.ui.hud import Hud
from wom.ui.renderer import MapRenderer

DOUBLE_CLICK_MS = 400  # ventana del doble click que fija la ruta


def _manhattan(a: Coord, b: Coord) -> int:
    return abs(a[0] - b[0]) + abs(a[1] - b[1])


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
        # Resaltado del ejército inicial: solo en partidas nuevas (turno 0),
        # para que el jugador encuentre rápido por dónde empieza.
        self.spawn_highlights: list[Coord] = (
            [a.position for a in game.armies_of(human_id)] if game.turn == 0 else []
        )
        self.spawn_highlight_start = pygame.time.get_ticks()
        # Fusión pendiente de confirmar: (source_id, target_id). Mientras
        # exista se muestra el modal de transferencia (merge_picker) y se
        # bloquea el resto del input.
        self.pending_merge: tuple[int, int] | None = None
        self.merge_picker: TroopPicker | None = None
        # División pendiente: id del ejército a dividir + su modal.
        self.pending_split: int | None = None
        self.split_picker: TroopPicker | None = None
        # Salida al menú pendiente de confirmar (ESC en plena partida).
        self.pending_quit = False
        self._dialog_buttons: dict[str, pygame.Rect] = {}
        # Doble click que fija la ruta: (ticks, tile) del último click de path.
        self._last_path_click: tuple[int, Coord] | None = None
        self._last_frame_ticks = 0  # para el dt del paneo por bordes

        # Layout sobre la resolución lógica: la app escala el canvas a la
        # ventana real (scale.present), acá no importa el tamaño físico.
        window = pygame.Rect((0, 0), theme.WINDOW_SIZE)
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
        if self.pending_merge is not None:
            choice = self.merge_picker.handle_event(event)
            if choice == "accept":
                self._confirm_merge()
            elif choice == "cancel":
                self.pending_merge = None
                self.merge_picker = None
            return
        if self.pending_split is not None:
            choice = self.split_picker.handle_event(event)
            if choice == "accept":
                self._confirm_split()
            elif choice == "cancel":
                self.pending_split = None
                self.split_picker = None
            return
        if self.pending_quit:
            choice = self._confirm_choice(event)
            if choice is True:
                self.wants_menu = True
                self.pending_quit = False
            elif choice is False:
                self.pending_quit = False
            return
        if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
            if self.game_over:
                self.wants_menu = True  # partida terminada: sale directo
            elif self.selected_id is not None or self.selected_fort is not None:
                # Primero deselecciona (la ruta trazada queda confirmada);
                # otro ESC recién abre el diálogo de salida.
                self.selected_id = None
                self.selected_fort = None
            else:
                self.pending_quit = True  # en juego: pide confirmación
            return
        if event.type == pygame.MOUSEWHEEL:
            # El zoom no toca el estado del juego: vale aún durante la
            # animación o con la partida terminada.
            if event.y:
                self.renderer.zoom(event.y, scale.mouse_pos())
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
        elif event.type == pygame.KEYDOWN and event.key == pygame.K_d:
            self._open_split()
        elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            if pygame.key.get_mods() & pygame.KMOD_SHIFT:
                self._shift_click(event.pos)
            elif self.hud.hit_end_turn(event.pos):
                self.end_turn()
            elif self.hud.hit_save(event.pos):
                self.save()
            elif self.selected_fort is not None and self.hud.hit_create_army(event.pos):
                self._toggle_creation(self.selected_fort)
            elif self.selected_id is not None and self.hud.hit_split(event.pos):
                self._open_split()
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
            if occupant.id == self.selected_id:
                # Click en el ya seleccionado: confirma la ruta y deselecciona
                # (permite después clickear un fuerte sin agregar waypoints).
                self.selected_id = None
            else:
                self.selected_id = occupant.id
                self.selected_fort = None
                self.spawn_highlights = []  # ya encontró su ejército
                self._last_path_click = None
            return
        if self.selected_id is not None:
            army = self.game.army_by_id(self.selected_id)
            if army is None:
                self.selected_id = None
                return
            now = pygame.time.get_ticks()
            if (
                self._last_path_click is not None
                and self._last_path_click[1] == tile
                and now - self._last_path_click[0] <= DOUBLE_CLICK_MS
            ):
                # Doble click en el destino: la ruta queda fijada y se libera
                # el foco (se puede clickear otra tropa sin sumar waypoints).
                self._last_path_click = None
                self.selected_id = None
                return
            self._extend_path(army.id, army.position, tile)
            self._last_path_click = (now, tile)
            return
        fort = self.game.world.fort_at(tile)
        if fort is not None and fort.owner == self.human_id:
            self.selected_fort = tile

    def _shift_click(self, point: tuple[int, int]) -> None:
        """Shift+click: elegir ejércitos propios para fusionar."""
        tile = self.renderer.screen_to_tile(point, self.game)
        if tile is None:
            return
        occupant = self.game.army_at(tile)
        if occupant is None or occupant.owner != self.human_id:
            return
        selected = (
            self.game.army_by_id(self.selected_id)
            if self.selected_id is not None else None
        )
        if selected is None or selected.id == occupant.id:
            self.selected_id = occupant.id  # primer shift+click: selecciona
            self.selected_fort = None
            return
        if _manhattan(selected.position, occupant.position) != 1:
            self._notify("Para fusionar deben estar en tiles aledaños")
            return
        max_size = self.game.config["max_army_size"]
        room = max_size - occupant.total_troops
        if room <= 0:
            self._notify(f"El ejército destino ya tiene {max_size} tropas")
            return
        rows = [
            (cid, self.game.classes[cid].nombre, count)
            for cid, count in sorted(selected.composition.items())
            if count > 0
        ]
        self.pending_merge = (selected.id, occupant.id)
        # Arranca con "Todo" (la fusión clásica); el jugador puede bajar
        # cantidades por clase para transferir solo una parte.
        self.merge_picker = TroopPicker(
            "Fusionar ejércitos",
            f"#{selected.id} ({selected.total_troops} tropas) → "
            f"#{occupant.id} ({occupant.total_troops} tropas) · máx {max_size}",
            rows,
            cap=room,
            initial=dict(selected.composition),
            accept_label="Fusionar (S)",
        )

    def _confirm_choice(self, event: pygame.event.Event) -> bool | None:
        """Input común de los diálogos de confirmación (modales).

        True = aceptó (S/Enter o botón sí), False = canceló (N/ESC o botón
        no), None = el evento no decide nada.
        """
        if event.type == pygame.KEYDOWN:
            if event.key in (pygame.K_s, pygame.K_RETURN):
                return True
            if event.key in (pygame.K_n, pygame.K_ESCAPE):
                return False
        elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            yes = self._dialog_buttons.get("yes")
            no = self._dialog_buttons.get("no")
            if yes is not None and yes.collidepoint(event.pos):
                return True
            if no is not None and no.collidepoint(event.pos):
                return False
        return None

    def _confirm_merge(self) -> None:
        source_id, target_id = self.pending_merge
        amounts = {c: n for c, n in self.merge_picker.amounts.items() if n > 0}
        self.pending_merge = None
        self.merge_picker = None
        if self.game.transfer_troops(source_id, target_id, amounts):
            self.selected_id = target_id  # el destino queda seleccionado
            if self.game.army_by_id(source_id) is None:
                self.pending_paths.pop(source_id, None)
                self._notify("Ejércitos fusionados")
            else:
                self._notify("Tropas transferidas")
        else:
            self._notify("No se pudo fusionar")

    def _open_split(self) -> None:
        """Abre el modal para dividir el ejército seleccionado en dos."""
        army = (
            self.game.army_by_id(self.selected_id)
            if self.selected_id is not None else None
        )
        if army is None or army.total_troops < 2:
            return
        rows = [
            (cid, self.game.classes[cid].nombre, count)
            for cid, count in sorted(army.composition.items())
            if count > 0
        ]
        self.pending_split = army.id
        self.split_picker = TroopPicker(
            "Dividir el ejército",
            f"Elegí qué tropas de #{army.id} ({army.total_troops}) forman el nuevo",
            rows,
            cap=army.total_troops - 1,  # siempre queda al menos una en el original
            accept_label="Dividir (S)",
        )

    def _confirm_split(self) -> None:
        army_id = self.pending_split
        amounts = {c: n for c, n in self.split_picker.amounts.items() if n > 0}
        self.pending_split = None
        self.split_picker = None
        created = self.game.split_army(army_id, amounts)
        if created is None:
            self._notify("No se pudo dividir: no hay tile libre aledaño")
        else:
            self.selected_id = created.id  # el nuevo queda seleccionado
            self._notify(
                f"Ejército dividido: #{created.id} con {created.total_troops} tropas"
            )

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
        self._notify(f"Partida guardada: {path.name}")

    def _notify(self, text: str) -> None:
        """Muestra un aviso temporal en el HUD."""
        self.notice = text
        self.notice_until = pygame.time.get_ticks() + 3000

    # --- turno ---------------------------------------------------------------

    def end_turn(self) -> None:
        self.animation = None  # descarta una animación anterior si la hubiera
        self.pending_merge = None
        self.merge_picker = None
        self.pending_split = None
        self.split_picker = None
        self.spawn_highlights = []  # con el juego en marcha ya no hace falta
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
        self._update_camera()  # paneo por bordes, una vez por frame
        animating = self.animating  # una sola lectura del reloj por frame
        elapsed = self._animation_elapsed() if animating else 0.0
        # Hasta que el choque de batalla revela el resultado, las cruces de
        # los muertos del turno no se muestran.
        revealed = not animating or self.animation.result_revealed(elapsed)
        # Con zoom el mapa excede su área: nada debe pisar el HUD.
        surface.set_clip(self.renderer.area)
        self.renderer.draw(
            surface, self.game, self.selected_id, self.pending_paths,
            self.selected_fort, self.pending_creations, hide_armies=animating,
            hide_new_crosses=not revealed,
        )
        if animating:
            for motion, pos in self.animation.positions(elapsed):
                self.renderer.draw_army_at(
                    surface, motion.owner, motion.class_id, motion.troops, pos
                )
            for point, intensity in self.animation.clash_effects(elapsed):
                self.renderer.draw_clash(surface, point, intensity)
        elif self.animation is not None:
            self.animation = None  # terminó: vuelve el dibujo normal
        if self.spawn_highlights:
            rings = spawn_highlight_rings(
                (pygame.time.get_ticks() - self.spawn_highlight_start) / 1000.0
            )
            if rings:
                for pos in self.spawn_highlights:
                    self.renderer.draw_tile_rings(
                        surface, pos, rings, theme.SPAWN_HIGHLIGHT
                    )
            else:
                self.spawn_highlights = []  # cumplió su tiempo
        surface.set_clip(None)
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
        if self.pending_merge is not None:
            self.merge_picker.draw(surface)
        elif self.pending_split is not None:
            self.split_picker.draw(surface)
        elif self.pending_quit:
            self._draw_confirm_dialog(
                surface, "¿Salir al menú?",
                "La partida no guardada se pierde", "Salir (S)",
            )

    def _update_camera(self) -> None:
        """Paneo por bordes: con el mouse pegado al borde del área de mapa la
        vista se desplaza (solo tiene efecto con zoom; sin zoom el clamp de
        la cámara mantiene el mapa centrado)."""
        now = pygame.time.get_ticks()
        dt = min((now - self._last_frame_ticks) / 1000.0, 0.1)
        self._last_frame_ticks = now
        blocked = (
            self.pending_merge is not None
            or self.pending_split is not None
            or self.pending_quit
            or self.game_over
        )
        if not blocked:
            self.renderer.camera.edge_pan(scale.mouse_pos(), dt)

    def _draw_confirm_dialog(
        self, surface: pygame.Surface, title: str, detail: str, yes_label: str
    ) -> None:
        """Diálogo modal centrado de confirmación (sí/no)."""
        overlay = pygame.Surface(surface.get_size(), pygame.SRCALPHA)
        overlay.fill(theme.GAMEOVER_BG)
        surface.blit(overlay, (0, 0))

        box = pygame.Rect(0, 0, 440, 150)
        box.center = surface.get_rect().center
        pygame.draw.rect(surface, theme.SIDEBAR_BG, box, border_radius=8)
        pygame.draw.rect(surface, theme.SELECTION, box, 2, border_radius=8)

        title_text = self.hud.font.render(title, True, theme.TEXT)
        detail_text = self.hud.small_font.render(detail, True, theme.TEXT_DIM)
        surface.blit(title_text, title_text.get_rect(midtop=(box.centerx, box.y + 18)))
        surface.blit(detail_text, detail_text.get_rect(midtop=(box.centerx, box.y + 50)))

        yes = pygame.Rect(box.x + 30, box.bottom - 56, 180, 38)
        no = pygame.Rect(box.right - 210, box.bottom - 56, 180, 38)
        mouse = scale.mouse_pos()
        for rect, text, base, hover in (
            (yes, yes_label, theme.BUTTON_BG, theme.BUTTON_BG_OVER),
            (no, "Cancelar (N)", (50, 56, 62), (70, 78, 86)),
        ):
            color = hover if rect.collidepoint(mouse) else base
            pygame.draw.rect(surface, color, rect, border_radius=6)
            label = self.hud.font.render(text, True, theme.TEXT)
            surface.blit(label, label.get_rect(center=rect.center))
        self._dialog_buttons = {"yes": yes, "no": no}
