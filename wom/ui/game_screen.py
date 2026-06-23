"""Pantalla de partida: input del jugador humano y orquestación del turno.

Interacción:
- Click izquierdo en un ejército propio: lo selecciona.
- Con un ejército seleccionado, click izquierdo en el mapa: traza el camino
  mínimo hasta ese tile (clicks sucesivos agregan waypoints). Otro click en
  el mismo ejército (o ESC) confirma la ruta y deselecciona; doble click en
  el destino hace lo mismo en un solo gesto (fija la ruta y libera el foco).
- Rueda del mouse: zoom in/out anclado al cursor; con zoom, llevar el mouse
  a los bordes de la ventana panea la vista (ver wom/ui/camera.py), y
  arrastrar con el botón del medio agarra el mapa y lo desplaza.
- Shift+click en un ejército propio: lo elige para fusionar; shift+click en
  otro propio aledaño abre el modal de transferencia, donde se elige "Todo"
  o la cantidad por clase que pasa al destino (Game.transfer_troops).
- Con un ejército seleccionado, botón "Dividir ejército" o tecla D: modal
  para elegir qué tropas por clase forman un ejército nuevo en un tile
  aledaño libre (Game.split_army).
- Sin ejército seleccionado, click en un fuerte propio: lo selecciona y el
  HUD ofrece "Crear ejército" (toma tropas de la reserva del fuerte).
- Click derecho: borra el camino trazado y deselecciona.
- Botón "Fin del turno" o Enter (también el del pad numérico): ejecuta el
  turno (humano + AI). El
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

import random

import pygame

from wom.ai.ai_player import AIPlayer, choose_personality
from wom.core.config import load_ai_personalities
from wom.core.game import Game
from wom.core.tactical import build_tactical_battle
from wom.core.orders import (
    CreateArmyOrder,
    MoveOrder,
    Order,
    SplitArmyOrder,
    TransferTroopsOrder,
)
from wom.core.pathfind import army_terrain_costs, shortest_path
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
from wom.ui.battle_screen import BattleScreen
from wom.ui.dialogs import TroopPicker
from wom.ui.help_overlay import HelpOverlay
from wom.ui.hud import Hud
from wom.ui.renderer import MapRenderer
from wom.ui.scenario_intro_overlay import ScenarioIntroOverlay

DOUBLE_CLICK_MS = 400  # ventana del doble click que fija la ruta


def _manhattan(a: Coord, b: Coord) -> int:
    return abs(a[0] - b[0]) + abs(a[1] - b[1])


class GameScreen:
    def __init__(
        self,
        game: Game,
        human_id: int = 0,
        ai_level: str = "facil",
        net=None,
        turn_seconds: int = 0,
        intro: ScenarioIntroOverlay | None = None,
    ):
        self.game = game
        self.human_id = human_id
        self.ai_level = ai_level  # fallback de nivel para el zoom de batalla
        # Intro del escenario: modal con título/descripción/imagen sobre el
        # mapa al empezar (None en partidas normales). Se cierra con un clic.
        self.scenario_intro = intro
        # Modo red (humano vs humano): el par aporta sus órdenes por la red en
        # vez de la AI, y el turno se resuelve cuando llegan las dos listas.
        self.net = net
        # Reloj de turno (0 = sin límite): al vencer, se envían las órdenes que
        # haya. Estado del chat del sidebar (modo red).
        self.turn_seconds = turn_seconds
        self._turn_deadline: int | None = None
        self.chat_active = False
        self.chat_buffer = ""
        # El nivel viaja en Player.ai_level (savegames); el parámetro es fallback.
        # La personalidad se deriva de la seed: misma partida => misma
        # personalidad, también al recargar un savegame. En red no hay AI.
        personalities = list(load_ai_personalities())
        self.ais = (
            []
            if net is not None
            else [
                AIPlayer(
                    p.id,
                    p.ai_level or ai_level,
                    personality=choose_personality(game.seed, p.id, personalities),
                )
                for p in game.players
                if p.is_ai
            ]
        )
        # Órdenes de reorganización (fusión/transferencia/división) que el
        # humano encola en modo red: no se aplican al instante (rompería el
        # lockstep), van con las órdenes del turno y se aplican al resolverlo.
        self.pending_reorg: list[Order] = []
        self._net_disconnect_shown = False
        self._net_desync_shown = False
        self.selected_id: int | None = None
        self.selected_fort: Coord | None = None
        self.pending_paths: dict[int, list[Coord]] = {}
        self.pending_creations: set[Coord] = set()
        self.result: VictoryResult | None = None
        self.wants_menu = False  # lo activa ESC; lo consume el loop de app
        self.wants_lobby = False  # red por servidor: volver al lobby, no al menú
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
        # Zoom de batalla (solo partida individual): tras begin_turn, las
        # batallas del humano se resuelven una a una preguntando "¿zoom o
        # auto-resolver?". `_tactical_queue` son las batallas que faltan
        # procesar este turno; `_tactical_prompt` la que espera la elección;
        # `_tactical_battle` la pantalla de combate activa; `_tactical_active`
        # el par (atacante, defensor) que se está dirigiendo; y `_tactical_pre`
        # el snapshot pre-turno para armar la animación de recap al terminar.
        self._tactical_queue: list[tuple[int, int]] = []
        self._tactical_prompt: tuple[int, int] | None = None
        self._tactical_battle: BattleScreen | None = None
        self._tactical_active: tuple[int, int] | None = None
        self._tactical_pre: list[dict] | None = None
        # Ayuda visual rápida (F1): modal por encima de todo, no toca el juego.
        self.help = HelpOverlay()
        self._dialog_buttons: dict[str, pygame.Rect] = {}
        # Doble click que fija la ruta: (ticks, tile) del último click de path.
        self._last_path_click: tuple[int, Coord] | None = None
        self._last_frame_ticks = 0  # para el dt del paneo por bordes
        # Grab con el botón del medio: última posición del mouse mientras
        # se arrastra el mapa, o None si no hay arrastre en curso.
        self._grab_anchor: tuple[int, int] | None = None

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
            net_mode=net is not None,
        )

    @property
    def game_over(self) -> bool:
        return self.result is not None and self.result.is_over

    @property
    def animating(self) -> bool:
        return self.animation is not None and not self.animation.finished(
            self._animation_elapsed()
        )

    @property
    def waiting_peer(self) -> bool:
        """En red: las órdenes locales ya se enviaron y falta el rival."""
        return self.net is not None and self.net.waiting

    @property
    def net_disconnected(self) -> bool:
        return self.net is not None and self.net.disconnected

    @property
    def capturing_text(self) -> bool:
        """True mientras se escribe en el chat: el loop suspende los atajos
        globales (p. ej. la M del reproductor) para que las teclas vayan acá."""
        return self.chat_active

    def _animation_elapsed(self) -> float:
        return (pygame.time.get_ticks() - self.animation_start) / 1000.0

    # --- input -------------------------------------------------------------

    def handle_event(self, event: pygame.event.Event) -> None:
        # Intro del escenario: modal dominante al empezar; mientras esté visible
        # traga todo el input (un clic/Enter/ESC la cierran).
        if self.scenario_intro is not None and self.scenario_intro.handle_event(event):
            return
        # Ayuda (F1): modal por encima de todo. Si está visible, traga el input;
        # si no, F1 la abre desde cualquier estado (salvo escribiendo en chat).
        if self.help.handle_event(event):
            return
        if (
            event.type == pygame.KEYDOWN
            and event.key == pygame.K_F1
            and not self.chat_active
        ):
            self.help.toggle()
            return
        # Combate táctico activo: la pantalla de batalla domina todo el input.
        if self._tactical_battle is not None:
            self._tactical_battle.handle_event(event)
            return
        # Modal "¿zoom o auto-resolver?": Z/Enter abre el zoom, A/ESC auto.
        if self._tactical_prompt is not None:
            if event.type == pygame.KEYDOWN:
                if event.key in (pygame.K_z, pygame.K_RETURN, pygame.K_KP_ENTER):
                    self._resolve_tactical_prompt(zoom=True)
                elif event.key in (pygame.K_a, pygame.K_ESCAPE):
                    self._resolve_tactical_prompt(zoom=False)
            elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                yes = self._dialog_buttons.get("yes")
                no = self._dialog_buttons.get("no")
                if yes is not None and yes.collidepoint(event.pos):
                    self._resolve_tactical_prompt(zoom=True)
                elif no is not None and no.collidepoint(event.pos):
                    self._resolve_tactical_prompt(zoom=False)
            return
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
                self._leave_to_menu()
                self.pending_quit = False
            elif choice is False:
                self.pending_quit = False
            return
        # El chat (modo red) se atiende antes que los bloqueos de animación o
        # de espera: se puede escribir en cualquier momento.
        if self.net is not None and self._handle_chat(event):
            return
        if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
            if self.game_over or self.net_disconnected:
                self._leave_to_menu()  # terminada o rival caído: sale directo
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
        # Grab con el botón del medio: como el zoom, solo mueve la vista,
        # así que también vale durante la animación o con la partida lista.
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 2:
            self._grab_anchor = event.pos
            return
        if event.type == pygame.MOUSEBUTTONUP and event.button == 2:
            self._grab_anchor = None
            return
        if event.type == pygame.MOUSEMOTION and self._grab_anchor is not None:
            if not event.buttons[1]:  # se soltó fuera de foco: corta el grab
                self._grab_anchor = None
                return
            dx = event.pos[0] - self._grab_anchor[0]
            dy = event.pos[1] - self._grab_anchor[1]
            self.renderer.camera.pan(-dx, -dy)  # el mapa sigue al mouse
            self._grab_anchor = event.pos
            return
        if self.animating:
            # Mientras anima solo se acepta saltearla; el estado ya es el final.
            if (
                event.type == pygame.MOUSEBUTTONDOWN
                or (event.type == pygame.KEYDOWN
                    and event.key in (
                        pygame.K_RETURN, pygame.K_KP_ENTER, pygame.K_SPACE
                    ))
            ):
                self.animation.skip()
            return
        if self.net_disconnected:
            return  # rival caído: no se aceptan más órdenes
        if self.waiting_peer:
            return  # órdenes enviadas: no se editan ni se reenvían hasta el rival
        if self.game_over:
            return
        if event.type == pygame.KEYDOWN and event.key in (
            pygame.K_RETURN, pygame.K_KP_ENTER
        ):
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
            f"#{selected.id} ({selected.total_troops} tropas) » "
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
            if event.key in (pygame.K_s, pygame.K_RETURN, pygame.K_KP_ENTER):
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

    def _leave_to_menu(self) -> None:
        """Pide salir de la partida.

        - En **servidor dedicado** (con la sesión viva): no cierra la conexión —
          deja la partida (`leave_match`) y vuelve al **lobby** (`wants_lobby`).
        - En LAN o un jugador: avisa al rival (Bye), cierra la sesión y vuelve
          al **menú**, para que el rival no quede esperando órdenes."""
        from wom.net.server_session import ServerSession

        if self.net is not None:
            if isinstance(self.net.session, ServerSession) and not self.net.disconnected:
                self.net.session.leave_match()
                self.wants_lobby = True
                return
            self.net.session.cancel("el rival salió de la partida")
        self.wants_menu = True

    def _confirm_merge(self) -> None:
        source_id, target_id = self.pending_merge
        amounts = {c: n for c, n in self.merge_picker.amounts.items() if n > 0}
        self.pending_merge = None
        self.merge_picker = None
        if not amounts:
            self._notify("No se pudo fusionar")
            return
        if self.net is not None:
            # En red la reorganización es diferida: viaja como orden y se aplica
            # al resolver el turno (igual que la AI), para no romper el lockstep.
            self.pending_reorg.append(
                TransferTroopsOrder(source_id, target_id, tuple(amounts.items()))
            )
            self._notify("Fusión encolada (se aplica al fin del turno)")
            return
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
        if not amounts:
            return
        if self.net is not None:
            self.pending_reorg.append(
                SplitArmyOrder(army_id, tuple(amounts.items()))
            )
            self._notify("División encolada (se aplica al fin del turno)")
            return
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
        army = self.game.army_by_id(army_id)
        terrain_costs = army_terrain_costs(
            self.game.config["costo_terreno"],
            army.composition if army else {},
            self.game.config.get("costo_terreno_clase"),
        )
        segment = shortest_path(self.game.world, start, target, terrain_costs)
        if segment:
            self.pending_paths[army_id] = current + segment

    # --- guardado --------------------------------------------------------------

    def save(self) -> None:
        """Guarda la partida con timestamp y muestra un aviso unos segundos."""
        if self.net is not None:
            self._notify("No se puede guardar una partida en red")
            return
        path = save_game(self.game)
        self._notify(f"Partida guardada: {path.name}")

    def _notify(self, text: str) -> None:
        """Muestra un aviso temporal en el HUD."""
        self.notice = text
        self.notice_until = pygame.time.get_ticks() + 3000

    # --- chat (modo red) -------------------------------------------------------

    def _handle_chat(self, event: pygame.event.Event) -> bool:
        """Maneja la entrada de chat. Devuelve True si consumió el evento."""
        if self.chat_active:
            if event.type == pygame.KEYDOWN:
                if event.key in (pygame.K_RETURN, pygame.K_KP_ENTER):
                    text = self.chat_buffer.strip()
                    if text:
                        self.net.send_chat(text)
                    self.chat_buffer = ""
                    self.chat_active = False
                elif event.key == pygame.K_ESCAPE:
                    self.chat_buffer = ""
                    self.chat_active = False
                elif event.key == pygame.K_BACKSPACE:
                    self.chat_buffer = self.chat_buffer[:-1]
                elif event.unicode and event.unicode.isprintable():
                    if len(self.chat_buffer) < 120:
                        self.chat_buffer += event.unicode
                return True
            if event.type == pygame.MOUSEBUTTONDOWN:
                self.chat_active = False  # click afuera: cierra el chat
                return True
            return False
        # Inactivo: T o click en la caja lo abren.
        if event.type == pygame.KEYDOWN and event.key == pygame.K_t:
            self.chat_active = True
            return True
        if (
            event.type == pygame.MOUSEBUTTONDOWN
            and event.button == 1
            and self.hud.hit_chat_input(event.pos)
        ):
            self.chat_active = True
            return True
        return False

    # --- turno ---------------------------------------------------------------

    def _local_orders(self) -> list[Order]:
        """Órdenes del jugador humano para este turno (paths, creaciones y la
        reorganización encolada en modo red)."""
        orders: list[Order] = [
            CreateArmyOrder(position=pos) for pos in self.pending_creations
        ]
        orders += [
            MoveOrder(army_id=army_id, path=tuple(path))
            for army_id, path in self.pending_paths.items()
            if path
        ]
        orders += self.pending_reorg
        return orders

    def end_turn(self) -> None:
        self.animation = None  # descarta una animación anterior si la hubiera
        self.pending_merge = None
        self.merge_picker = None
        self.pending_split = None
        self.split_picker = None
        self.spawn_highlights = []  # con el juego en marcha ya no hace falta
        if self.net is not None:
            self._end_turn_net()
            return
        orders = self._local_orders()
        for ai in self.ais:
            orders.extend(ai.decide_orders(self.game))
        # Snapshot pre-turno: conserva sprite/posición de los que mueran.
        pre_turn = [army.to_dict() for army in self.game.armies]
        self.pending_paths.clear()
        self.pending_creations.clear()
        self.selected_id = None
        self.selected_fort = None
        # Mueve los ejércitos y deja la cola de batallas. Si alguna involucra
        # al humano, se resuelven con el flujo de zoom (pregunta por batalla);
        # si no, es el camino de siempre (resolver todo + animar).
        pending = self.game.begin_turn(orders)
        if any(self._involves_human(a, d) for a, d in pending):
            self._tactical_queue = list(pending)
            self._tactical_pre = pre_turn
            self._advance_tactical_queue()
        else:
            for attacker_id, defender_id in pending:
                self.game.resolve_one_battle(attacker_id, defender_id)
            self._finish_turn_animation(pre_turn)

    def _finish_turn_animation(self, pre_turn: list[dict]) -> None:
        """Cierra el turno (finish_turn) y arma la animación de recap."""
        self.result = self.game.finish_turn()
        self.animation = build_turn_animation(self.game, pre_turn)
        self.animation_start = pygame.time.get_ticks()

    def _involves_human(self, attacker_id: int, defender_id: int) -> bool:
        for army_id in (attacker_id, defender_id):
            army = self.game.army_by_id(army_id)
            if army is not None and army.owner == self.human_id:
                return True
        return False

    def _advance_tactical_queue(self) -> None:
        """Procesa la próxima batalla de la cola del turno.

        Las del humano abren el modal "¿zoom o auto-resolver?"; las de IA
        contra IA se auto-resuelven sin preguntar. Al vaciarse la cola, cierra
        el turno y dispara la animación de recap (marcha + choque + retirada).
        """
        while self._tactical_queue:
            attacker_id, defender_id = self._tactical_queue.pop(0)
            attacker = self.game.army_by_id(attacker_id)
            defender = self.game.army_by_id(defender_id)
            if attacker is None or defender is None:
                continue  # destruido en una batalla previa: resolve la ignora
            if self._involves_human(attacker_id, defender_id):
                self._tactical_prompt = (attacker_id, defender_id)
                return  # espera la elección del jugador (modal)
            self.game.resolve_one_battle(attacker_id, defender_id)
        pre_turn, self._tactical_pre = self._tactical_pre, None
        self._finish_turn_animation(pre_turn or [])

    def _resolve_tactical_prompt(self, zoom: bool) -> None:
        """El jugador eligió zoom (combate dirigido) o auto-resolver."""
        attacker_id, defender_id = self._tactical_prompt
        self._tactical_prompt = None
        if not zoom:
            self.game.resolve_one_battle(attacker_id, defender_id)
            self._advance_tactical_queue()
            return
        attacker = self.game.army_by_id(attacker_id)
        defender = self.game.army_by_id(defender_id)
        # RNG propia: no toca game.rng, así el flujo determinista del core
        # (producción, futuras batallas auto) queda intacto elija lo que elija.
        battle = build_tactical_battle(
            attacker, defender, self.game.world, self.game.classes,
            self.game.config["batalla"], random.Random(),
        )
        enemy_owner = (
            defender.owner if attacker.owner == self.human_id else attacker.owner
        )
        self._tactical_battle = BattleScreen(
            battle, human_owner=self.human_id,
            enemy_level=self._enemy_ai_level(enemy_owner),
        )
        self._tactical_active = (attacker_id, defender_id)

    def _enemy_ai_level(self, owner: int) -> str:
        """Nivel de IA del rival en una batalla (el de su jugador, o el fallback)."""
        for ai in self.ais:
            if ai.player_id == owner:
                return ai.level
        return self.ai_level

    def _finish_tactical_battle(self) -> None:
        """La pantalla de combate terminó: aplica su resultado al core."""
        attacker_id, defender_id = self._tactical_active
        bs = self._tactical_battle
        result = None if bs.auto_resolve else bs.result
        self.game.resolve_one_battle(attacker_id, defender_id, result=result)
        self._tactical_battle = None
        self._tactical_active = None
        self._advance_tactical_queue()

    def _end_turn_net(self) -> None:
        """Modo red: envía las órdenes locales y espera al rival; el turno se
        ejecuta en `update()` cuando llegan las dos listas."""
        self.net.submit_local_orders(self._local_orders())
        self.pending_paths.clear()
        self.pending_creations.clear()
        self.pending_reorg = []
        self.selected_id = None
        self.selected_fort = None
        # Aviso persistente mientras se espera (lo limpia update() al resolver).
        self.notice = "Esperando al rival…"
        self.notice_until = pygame.time.get_ticks() + 10**9

    def update(self) -> None:
        """Una vez por frame (la llama el loop de la app). Conduce el combate
        táctico si hay uno activo; en red conduce el lockstep y dispara la
        animación de cada turno; sin red ni batalla, no hace nada."""
        if self._tactical_battle is not None:
            self._tactical_battle.update()
            if self._tactical_battle.done:
                self._finish_tactical_battle()
            return
        if self.net is None:
            return
        self.net.update()
        resolved = self.net.consume_resolved()
        if resolved is not None:
            self.result, pre_turn = resolved
            self.animation = build_turn_animation(self.game, pre_turn)
            self.animation_start = pygame.time.get_ticks()
            self.notice = None  # se acabó la espera
        if self.net.resynced:
            self.net.resynced = False
            # El estado se reemplazó por el del host: descarto las órdenes en
            # curso (referían al estado viejo) y deselecciono.
            self.pending_paths.clear()
            self.pending_creations.clear()
            self.pending_reorg = []
            self.selected_id = None
            self.selected_fort = None
            self.animation = None
            self._notify("Resincronizado con el host")
        if self.net.desync and not self._net_desync_shown:
            self._net_desync_shown = True  # host: avisa que resincroniza al rival
            self._notify("Divergencia detectada: resincronizando al rival")
        if self.net.disconnected and not self._net_disconnect_shown:
            self._net_disconnect_shown = True
            self._notify(f"Rival desconectado: {self.net.disconnect_reason}")
        self._update_turn_timer()

    def _update_turn_timer(self) -> None:
        """Reloj de turno: cuenta solo mientras el humano puede dar órdenes; al
        vencer envía el turno con lo que haya (como el humano apurado)."""
        if self.turn_seconds <= 0 or self.game_over:
            return
        if self.waiting_peer or self.animating:
            self._turn_deadline = None  # no corre mientras espera o anima
            return
        now = pygame.time.get_ticks()
        if self._turn_deadline is None:
            self._turn_deadline = now + self.turn_seconds * 1000
        elif now >= self._turn_deadline:
            self._turn_deadline = None
            self.end_turn()

    # --- dibujo ----------------------------------------------------------------

    def draw(self, surface: pygame.Surface) -> None:
        # Combate táctico: ocupa toda la pantalla por encima del mapa.
        if self._tactical_battle is not None:
            self._tactical_battle.draw(surface)
            return
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
            net_panel=self._net_panel(animating),
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
        elif self._tactical_prompt is not None:
            self._draw_tactical_prompt(surface)
        # La ayuda va arriba de todo (incluidos los modales).
        self.help.draw(surface, self.game)
        # La intro del escenario, si sigue abierta, manda sobre todo lo demás.
        if self.scenario_intro is not None:
            self.scenario_intro.draw(surface)

    def _net_panel(self, animating: bool) -> dict | None:
        """Datos para el panel de red del HUD (chat, estado, reloj de turno)."""
        if self.net is None:
            return None
        seconds_left = None
        if (
            self.turn_seconds > 0
            and self._turn_deadline is not None
            and not self.waiting_peer
            and not animating
            and not self.game_over
        ):
            remaining = self._turn_deadline - pygame.time.get_ticks()
            seconds_left = max(0, (remaining + 999) // 1000)
        return {
            "chat_log": self.net.chat_log,
            "chat_active": self.chat_active,
            "chat_buffer": self.chat_buffer,
            "seconds_left": seconds_left,
            "peer_name": self.net.peer_name,
            "waiting": self.waiting_peer,
            "disconnected": self.net.disconnected,
        }

    def _update_camera(self) -> None:
        """Paneo por bordes: con el mouse pegado al borde de la ventana la
        vista se desplaza (solo tiene efecto con zoom; sin zoom el clamp de
        la cámara mantiene el mapa centrado). Se pasa la ventana entera como
        bounds para que a la derecha active el borde real de la pantalla y
        no el límite del mapa con el HUD."""
        now = pygame.time.get_ticks()
        dt = min((now - self._last_frame_ticks) / 1000.0, 0.1)
        self._last_frame_ticks = now
        blocked = (
            self.pending_merge is not None
            or self.pending_split is not None
            or self.pending_quit
            or self.game_over
            or self._grab_anchor is not None  # el grab manda mientras dure
            or (self.scenario_intro is not None and self.scenario_intro.visible)
        )
        if not blocked:
            self.renderer.camera.edge_pan(
                scale.mouse_pos(), dt, (0, 0, *theme.WINDOW_SIZE)
            )

    def _draw_tactical_prompt(self, surface: pygame.Surface) -> None:
        """Modal por batalla: dirigir el combate (zoom) o que lo resuelva el
        motor. Muestra quién pelea y dónde."""
        attacker_id, defender_id = self._tactical_prompt
        attacker = self.game.army_by_id(attacker_id)
        defender = self.game.army_by_id(defender_id)
        en_fuerte = (
            defender is not None
            and self.game.world.fort_at(defender.position) is not None
        )
        lugar = "Asalto a fuerte" if en_fuerte else "Batalla en campo abierto"
        atk_n = attacker.total_troops if attacker else 0
        def_n = defender.total_troops if defender else 0

        overlay = pygame.Surface(surface.get_size(), pygame.SRCALPHA)
        overlay.fill(theme.GAMEOVER_BG)
        surface.blit(overlay, (0, 0))
        box = pygame.Rect(0, 0, 520, 180)
        box.center = surface.get_rect().center
        pygame.draw.rect(surface, theme.SIDEBAR_BG, box, border_radius=8)
        pygame.draw.rect(surface, theme.SELECTION, box, 2, border_radius=8)

        title = self.hud.font.render(f"¡{lugar}!", True, theme.TEXT)
        detail = self.hud.small_font.render(
            f"Tus tropas: {atk_n if attacker and attacker.owner == self.human_id else def_n}"
            f"   ·   Enemigo: {def_n if attacker and attacker.owner == self.human_id else atk_n}",
            True, theme.TEXT_DIM,
        )
        surface.blit(title, title.get_rect(midtop=(box.centerx, box.y + 16)))
        surface.blit(detail, detail.get_rect(midtop=(box.centerx, box.y + 48)))

        yes = pygame.Rect(box.x + 30, box.bottom - 58, 210, 40)
        no = pygame.Rect(box.right - 240, box.bottom - 58, 210, 40)
        mouse = scale.mouse_pos()
        for rect, text, base, hover in (
            (yes, "Dirigir batalla (Z)", theme.BUTTON_BG, theme.BUTTON_BG_OVER),
            (no, "Auto-resolver (A)", (50, 56, 62), (70, 78, 86)),
        ):
            color = hover if rect.collidepoint(mouse) else base
            pygame.draw.rect(surface, color, rect, border_radius=6)
            label = self.hud.font.render(text, True, theme.TEXT)
            surface.blit(label, label.get_rect(center=rect.center))
        self._dialog_buttons = {"yes": yes, "no": no}

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
