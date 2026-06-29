"""Pantalla del combate táctico en tiempo real (el "zoom" de batalla).

La conduce `GameScreen` como un modal de pantalla completa: mientras hay una
batalla activa le delega `handle_event`/`update`/`draw`. Renderiza la
`TacticalBattle` (modelo puro de `wom/core/tactical.py`), deja que el jugador
seleccione y dé órdenes a sus fichas en vivo (caja de selección, mover/atacar
con clic derecho, aguantar con H) y corre la `TacticalAI` para el rival **con el
nivel de su ejército** (facil/medio/dificil, vía `enemy_level`).

Arranca en una **fase de preparación** (pausada): las fichas del humano
empiezan en "aguantar" (no atacan hasta que se les ordena) para que el general
decida la táctica. Durante la cuenta regresiva el jugador puede elegir una
**formación de despliegue** con 1/2/3/4 (línea/clásica/compacta/en V), que
reordena sus tropas; la IA del rival elige la suya según su composición. Con
Espacio (o el botón "Comenzar") arranca el combate. Cada
ficha se enmarca con el **color de su jugador** (rojo J1, azul J2, verde J3,
amarillo J4) y muestra sus tropas, igual que en el mapa. Los **arqueros**
disparan a distancia con un efecto de flecha.

Cuando la batalla termina (o el jugador toca "Auto-resolver") expone:
- `done`: la pantalla terminó y `GameScreen` debe cerrarla.
- `auto_resolve`: el jugador pidió que el motor resuelva (resultado determinista).
- `result`: el `BattleResult` del combate dirigido (si no fue auto-resuelto).

Es la única capa con pygame de todo el feature; el modelo y la IA son puros.
"""

from __future__ import annotations

import math

import pygame

from wom.ai.tactical_ai import TacticalAI
from wom.core import formations
from wom.core.battle import BattleOutcome, BattleResult
from wom.core.tactical import TacticalBattle, Unit
from wom.ui import scale, theme
from wom.ui.assets import load_image, load_scaled

# Fondo del campo abierto (cielo+montañas arriba, pradera abajo). El campo de
# juego se ubica en la pradera (debajo del horizonte). Solo campo abierto; el
# castillo usa el render por celdas hasta tener su propio asset.
OPEN_FIELD_BG = "fondo-batalla-terreno"   # fondo de campo abierto (pradera)
FORT_BG = "fondo-batalla-fuerte"          # fondo del asalto a fuerte (muralla+puerta)
HORIZON_OPEN = 0.40   # campo abierto: el horizonte termina ~40% (pradera abajo)
HORIZON_FORT = 0.33   # fuerte: el terreno arranca ~1/3 (cielo arriba)
SHEAR_FORT = 0.32     # inclinación por perspectiva del patio del fuerte (0 = sin)
DEPTH_FAR = 0.8       # escala de las tropas más lejanas (arriba, cerca del horizonte)
DEPTH_NEAR = 1.0      # escala de las más cercanas al observador (abajo)
SPRITE_SCALE = 1.0    # tamaño del sprite respecto de la celda (más grande = más legible)
DISC_SCALE = 0.58     # radio del disco/anillo del dueño respecto de la celda
DISC_ALPHA = 105      # opacidad del disco (translúcido: deja ver la pradera detrás)

FIELD_BG = {
    "plains": (74, 104, 58),
    "forest": (48, 80, 46),
    "mountain": (96, 90, 78),
    "water": (60, 90, 130),
    "bridge_h": (120, 95, 60),
    "bridge_v": (120, 95, 60),
}
WALL_COLOR = (110, 108, 100)
WALL_TOP = (140, 138, 128)
GATE_COLOR = (90, 70, 45)
HUD_H = 70
SELECT_COLOR = (250, 220, 60)
ATTACK_LINE = (255, 120, 90)
PREP_SECONDS = 10.0  # cuenta regresiva de preparación antes del combate

# Teclas 1..4 (fila superior y teclado numérico) → formación de despliegue.
_FORMATION_KEYS = {
    pygame.K_1: formations.LINE, pygame.K_KP1: formations.LINE,
    pygame.K_2: formations.CLASSIC, pygame.K_KP2: formations.CLASSIC,
    pygame.K_3: formations.COMPACT, pygame.K_KP3: formations.COMPACT,
    pygame.K_4: formations.VEE, pygame.K_KP4: formations.VEE,
}

def _lighten(color: tuple[int, int, int], factor: float = 0.45) -> tuple[int, int, int]:
    """Versión aclarada de un color (para el disco translúcido bajo el sprite)."""
    return tuple(int(c + (255 - c) * factor) for c in color)


OUTCOME_TEXT = {
    BattleOutcome.ATTACKER_WINS: "Victoria del atacante",
    BattleOutcome.DEFENDER_WINS: "Victoria del defensor",
    BattleOutcome.ATTACKER_RETREATS: "El atacante se retira",
    BattleOutcome.DEFENDER_RETREATS: "El defensor se retira",
    BattleOutcome.DRAW: "Empate",
}


class BattleScreen:
    """Render + input del combate táctico. La conduce GameScreen."""

    def __init__(
        self, battle: TacticalBattle, human_owner: int, enemy_level: str | None = None,
        *, net=None, participant: bool = True,
    ):
        self.battle = battle
        self.human_owner = human_owner
        # En red el modelo lo simula la autoridad (NetGame/MatchRunner): esta
        # pantalla solo renderiza y enruta el input. `participant` distingue al
        # jugador que da órdenes del espectador (mira, sin input).
        self.net = net
        self.participant = participant if net is not None else True
        self.enemy_owner = (
            battle.defender_owner
            if human_owner == battle.attacker_owner
            else battle.attacker_owner
        )
        # La IA del rival solo corre en single-player; en red la conduce la
        # autoridad junto con la simulación.
        if net is None:
            self.ai = TacticalAI.for_level(self.enemy_owner, enemy_level)
            self.ai.plan_formation(battle)
        else:
            self.ai = None
        # Formación activa del humano (arranca en línea); el jugador la cambia
        # con 1/2/3/4 durante la preparación.
        self.formation = battle.formation_of(human_owner)
        self.selected: set[int] = set()
        self._drag_start: tuple[int, int] | None = None
        self._drag_now: tuple[int, int] | None = None
        self.paused = False
        self.done = False
        self.auto_resolve = False
        self.result: BattleResult | None = None
        self._last_ticks: int | None = None
        self._sprite_cache: dict[tuple[str, int], pygame.Surface] = {}
        # Fondo según el escenario: pradera (campo abierto) o fuerte (con
        # muralla+puerta dibujadas). Se carga una vez y se escala al área en
        # _layout; con fondo, el campo se ubica en el terreno (debajo del
        # horizonte) y las tropas escalan por profundidad. Si falta el asset,
        # degrada al render por celdas.
        is_fort = bool(battle.walls)
        self._bg_src = load_image(FORT_BG if is_fort else OPEN_FIELD_BG)
        self.has_bg = self._bg_src is not None
        self._horizon = HORIZON_FORT if is_fort else HORIZON_OPEN
        # Shear de perspectiva: inclina la grilla del fuerte para alinear patio
        # y muralla con el dibujo (0 en campo abierto / sin fondo).
        self._shear = SHEAR_FORT if (is_fort and self.has_bg) else 0.0
        self._bg_scaled: pygame.Surface | None = None
        # Efectos de flecha en vuelo (solo visual): cada uno
        # {"from","to","t0","dur"}. Se generan cuando un arquero ataca.
        self._arrows: list[dict] = []

        # Fase: "planning" da unos segundos (cuenta regresiva) para que el
        # general ordene a sus tropas; "fighting" corre la simulación. Las
        # fichas del humano arrancan en "aguantar" (no atacan hasta que se les
        # ordena). El combate empieza solo al llegar el contador a 0 (o antes
        # con Espacio / el botón "Comenzar").
        self.phase = "planning"
        self._countdown = PREP_SECONDS
        if net is None:
            # Las fichas del humano arrancan en "aguantar" (en red ya lo hizo la
            # autoridad sobre el modelo compartido).
            self.battle.command([u.id for u in self._my_units()], "hold")

        self.title_font = pygame.font.SysFont(None, 56)
        self.font = pygame.font.SysFont(None, 28)
        self.small_font = pygame.font.SysFont(None, 22)
        self.count_font = pygame.font.SysFont(None, 20)
        self._layout(theme.WINDOW_SIZE)

    # --- layout / transformación de coordenadas --------------------------
    def _layout(self, window: tuple[int, int]) -> None:
        w, h = window
        self.win_w, self.win_h = w, h
        area_h = h - HUD_H
        self._area_rect = pygame.Rect(0, HUD_H, w, area_h)
        if self.has_bg:
            # Campo en la pradera (debajo del horizonte): banda inferior.
            band_top = HUD_H + area_h * self._horizon
            band_h = area_h * (1.0 - self._horizon)
            self._bg_scaled = pygame.transform.scale(self._bg_src, (w, area_h))
        else:
            band_top = HUD_H
            band_h = area_h
        fh = self.battle.field_h
        # Inclinación por perspectiva (solo fuerte): la grilla se ensancha
        # `_shear * fh` celdas (arriba a la derecha), así que la celda y el
        # centrado horizontal la contemplan.
        eff_w = self.battle.field_w + self._shear * fh
        self.cell = min(w / eff_w, band_h / fh)
        self.ox = (w - self.cell * eff_w) / 2
        self.oy = band_top + (band_h - self.cell * fh) / 2
        self._auto_btn = pygame.Rect(w - 230, 12, 210, HUD_H - 24)
        # Botón "Comenzar" (solo en preparación), a la izquierda del de auto.
        self._start_btn = pygame.Rect(w - 470, 12, 220, HUD_H - 24)
        self._layout_formation_chooser(w)

    def _layout_formation_chooser(self, w: int) -> None:
        """Rects clickeables de cada opción de formación (panel de la cuenta).

        Se calculan acá (no en el dibujo) para que el clic use la misma
        geometría. El panel va centrado arriba; esta fila, cerca de su base."""
        cx = w // 2
        cy = HUD_H + 12 + 134 - 26  # base del panel de cuenta regresiva
        texts = [
            f"{i + 1} {formations.FORMATION_NAMES[key]}"
            for i, key in enumerate(formations.FORMATIONS)
        ]
        widths = [self.small_font.size(t)[0] for t in texts]
        gap = 18
        x = cx - (sum(widths) + gap * (len(texts) - 1)) // 2
        self._formation_rects: dict[str, tuple[pygame.Rect, str]] = {}
        for key, text, width in zip(formations.FORMATIONS, texts, widths):
            rect = pygame.Rect(0, 0, width, self.small_font.get_height())
            rect.midleft = (x, cy)
            self._formation_rects[key] = (rect, text)
            x += width + gap

    def to_px(self, x: float, y: float) -> tuple[int, int]:
        # Shear por profundidad (fuerte): arriba/lejos corre a la derecha para
        # seguir la perspectiva del patio; 0 en campo abierto.
        sx = x + self._shear * (self.battle.field_h - y)
        return int(self.ox + sx * self.cell), int(self.oy + y * self.cell)

    def to_cell(self, px: float, py: float) -> tuple[float, float]:
        # Inversa de to_px: primero la fila (y), luego deshace el shear en x.
        cy = (py - self.oy) / self.cell
        cx = (px - self.ox) / self.cell - self._shear * (self.battle.field_h - cy)
        return cx, cy

    def _depth(self, cell_y: float) -> float:
        """Escala por profundidad: 0.8 arriba (lejos) → 1.0 abajo (cerca).

        Da un pseudo-3D barato sobre el fondo con horizonte. Sin fondo (campo
        por celdas / castillo) no se aplica."""
        if not self.has_bg:
            return 1.0
        frac = max(0.0, min(1.0, cell_y / max(1, self.battle.field_h)))
        return DEPTH_FAR + (DEPTH_NEAR - DEPTH_FAR) * frac

    def _sprite(self, class_id: str, depth: float = 1.0) -> pygame.Surface:
        size = max(10, int(self.cell * SPRITE_SCALE * depth))
        key = (class_id, size)
        if key not in self._sprite_cache:
            self._sprite_cache[key] = load_scaled(class_id, size)
        return self._sprite_cache[key]

    # --- input -----------------------------------------------------------
    def handle_event(self, event: pygame.event.Event) -> None:
        if self.done:
            return
        if event.type == pygame.VIDEORESIZE:
            self._layout((event.w, event.h))
            return
        if self.result is not None:
            # Batalla terminada: cualquier clic/Enter cierra el resumen.
            if event.type == pygame.MOUSEBUTTONDOWN or (
                event.type == pygame.KEYDOWN
                and event.key in (pygame.K_RETURN, pygame.K_ESCAPE, pygame.K_SPACE)
            ):
                self.done = True
            return
        if event.type == pygame.KEYDOWN:
            self._on_key(event.key)
        elif event.type == pygame.MOUSEBUTTONDOWN:
            self._on_mouse_down(event)
        elif event.type == pygame.MOUSEBUTTONUP and event.button == 1:
            self._on_mouse_up(event)
        elif event.type == pygame.MOUSEMOTION and self._drag_start is not None:
            self._drag_now = event.pos

    def _on_key(self, key: int) -> None:
        if key in (pygame.K_SPACE, pygame.K_RETURN, pygame.K_KP_ENTER):
            if self.phase == "planning":
                self._begin_fight()  # arranca el combate / marca "listo"
            return
        if self.phase == "planning" and key in _FORMATION_KEYS:
            self._apply_formation(_FORMATION_KEYS[key])
            return
        if key == pygame.K_ESCAPE:
            if self.net is None:  # en red no se puede pausar la simulación remota
                self.paused = not self.paused
        elif key in (pygame.K_h,):
            self._cmd(self.selected, "hold")
        elif key in (pygame.K_a,):  # seleccionar todas mis fichas
            self.selected = {u.id for u in self._my_units()}

    def _on_mouse_down(self, event: pygame.event.Event) -> None:
        if event.button == 1:
            # El botón de auto-resolver solo existe en single-player (en red la
            # batalla la resuelve la autoridad por modo/voto/desconexión).
            if self.net is None and self._auto_btn.collidepoint(event.pos):
                self.auto_resolve = True
                self.done = True
                return
            if self.phase == "planning" and self._start_btn.collidepoint(event.pos):
                self._begin_fight()
                return
            if self.phase == "planning" and self._click_formation(event.pos):
                return
            self._drag_start = event.pos
            self._drag_now = event.pos
        elif event.button == 3 and self.selected:
            self._issue_order(event.pos)

    def _click_formation(self, pos: tuple[int, int]) -> bool:
        """Clic en una etiqueta de formación del panel: la aplica. Devuelve True
        si tomó el clic (para no iniciar una caja de selección)."""
        for key, (rect, _text) in self._formation_rects.items():
            if rect.inflate(10, 6).collidepoint(pos):
                self._apply_formation(key)
                return True
        return False

    # --- ruteo de input (single-player muta el modelo; red lo envía) ------
    def _cmd(self, unit_ids, kind: str, target=None) -> None:
        ids = list(unit_ids)
        if not ids:
            return
        if self.net is None:
            self.battle.command(ids, kind, target)
        elif self.participant:
            self.net.battle_command(ids, kind, target)

    def _apply_formation(self, formation: str) -> None:
        self.formation = formation
        if self.net is None:
            self.battle.set_formation(self.human_owner, formation)
        elif self.participant:
            self.net.battle_set_formation(formation)

    def _begin_fight(self) -> None:
        if self.net is None:
            self.phase = "fighting"
        elif self.participant:
            self.net.battle_ready()

    def _on_mouse_up(self, event: pygame.event.Event) -> None:
        if self._drag_start is None:
            return
        x0, y0 = self._drag_start
        x1, y1 = event.pos
        box = pygame.Rect(min(x0, x1), min(y0, y1), abs(x1 - x0), abs(y1 - y0))
        if box.width < 10 and box.height < 10:
            self._select_at(event.pos)  # clic: selecciona una sola unidad
        else:
            sel = {
                u.id for u in self._my_units() if box.collidepoint(self.to_px(u.x, u.y))
            }
            # Arrastre que no tomó a nadie: tratarlo como clic en el punto final.
            if sel:
                self.selected = sel
            else:
                self._select_at(event.pos)
        self._drag_start = self._drag_now = None

    def _select_at(self, pos: tuple[int, int]) -> None:
        unit = self._unit_at(pos, owner=self.human_owner)
        self.selected = {unit.id} if unit is not None else set()

    def _issue_order(self, pos: tuple[int, int]) -> None:
        enemy = self._unit_at(pos, owner=self.enemy_owner)
        if enemy is not None:
            self._cmd(self.selected, "attack", enemy.id)
        else:
            cx, cy = self.to_cell(*pos)
            self._cmd(self.selected, "move", (cx, cy))

    def _unit_at(self, pos: tuple[int, int], *, owner: int) -> Unit | None:
        best, best_d = None, (self.cell * 0.7) ** 2
        for u in self.battle.units:
            if u.owner != owner or not u.active:
                continue
            px, py = self.to_px(u.x, u.y)
            d = (px - pos[0]) ** 2 + (py - pos[1]) ** 2
            if d < best_d:
                best, best_d = u, d
        return best

    def _my_units(self) -> list[Unit]:
        return [u for u in self.battle.units if u.owner == self.human_owner and u.active]

    # --- simulación ------------------------------------------------------
    def update(self) -> None:
        ticks = pygame.time.get_ticks()
        if self._last_ticks is None:
            self._last_ticks = ticks
            return
        dt = (ticks - self._last_ticks) / 1000.0
        self._last_ticks = ticks
        if self.net is not None:
            self._update_net(dt)
            return
        if self.done or self.paused or self.result is not None:
            return
        if self.phase == "planning":
            # Cuenta regresiva en tiempo real; al llegar a 0 empieza el combate.
            self._countdown = max(0.0, self._countdown - min(dt, 0.2))
            if self._countdown <= 0.0:
                self.phase = "fighting"
            return
        dt = min(dt, 0.05)  # acota saltos grandes (ventana arrastrada, lag)
        self.ai.update(self.battle, dt)
        self.battle.step(dt)
        self._spawn_arrows()
        self._age_arrows(dt)
        self.selected = {uid for uid in self.selected if self._alive(uid)}
        if self.battle.finished and self.result is None:
            self.result = self.battle.to_battle_result()

    def _update_net(self, dt: float) -> None:
        """En red la autoridad simula: solo seguimos su fase/cuenta y animamos.

        El modelo (`self.battle`) lo actualiza `NetGame` (host: el sim vivo;
        cliente: el espejo de snapshots). Acá no se llama a `step`/`ai`."""
        phase = self.net.battle_phase()
        self.phase = "fighting" if phase == "fighting" else "planning"
        self._countdown = self.net.battle_countdown()
        # Interpola el espejo del cliente hacia la última posición autoritativa
        # (no-op en el host, que ya tiene el sim vivo a ritmo de frame).
        self.net.battle_interpolate(dt)
        if self.net.is_host:
            self._spawn_arrows()  # del sim vivo (attack_events del último step)
        else:
            for seg in self.net.battle_arrows():
                self._arrows.append(
                    {"from": (seg[0], seg[1]), "to": (seg[2], seg[3]), "t": 0.0, "dur": 0.22}
                )
        self._age_arrows(dt)
        self.selected = {uid for uid in self.selected if self._alive(uid)}

    def _spawn_arrows(self) -> None:
        """Crea una flecha por cada ataque de arquero de este step (visual)."""
        classes = self.battle.classes
        for attacker_id, target_id in self.battle.attack_events:
            atk = self.battle.unit_by_id(attacker_id)
            tgt = self.battle.unit_by_id(target_id)
            if atk is None or tgt is None:
                continue
            if not classes[atk.class_id].ignora_bonus_fort:
                continue  # solo los arqueros disparan flechas
            self._arrows.append(
                {"from": (atk.x, atk.y), "to": (tgt.x, tgt.y), "t": 0.0, "dur": 0.22}
            )

    def _age_arrows(self, dt: float) -> None:
        for arrow in self._arrows:
            arrow["t"] += dt
        self._arrows = [a for a in self._arrows if a["t"] < a["dur"]]

    def _alive(self, uid: int) -> bool:
        u = self.battle.unit_by_id(uid)
        return u is not None and u.active

    # --- render ----------------------------------------------------------
    def draw(self, surface: pygame.Surface) -> None:
        if (surface.get_width(), surface.get_height()) != (self.win_w, self.win_h):
            self._layout(surface.get_size())
        self._draw_field(surface)
        self._draw_walls(surface)
        self._draw_units(surface)
        self._draw_arrows(surface)
        self._draw_drag_box(surface)
        self._draw_hud(surface)
        if self.result is not None:
            self._draw_result(surface)
        elif self.phase == "planning":
            self._draw_countdown(surface)  # contador, sin tapar el campo
        elif self.paused:
            self._draw_center_banner(surface, "Pausa (ESC para seguir)")

    def _draw_field(self, surface: pygame.Surface) -> None:
        surface.fill((24, 26, 24))
        if self.has_bg and self._bg_scaled is not None:
            surface.blit(self._bg_scaled, self._area_rect.topleft)
            return
        b = self.battle
        mid = self.ox + self.cell * b.field_w / 2
        atk_col = FIELD_BG.get(b.attacker_terrain, FIELD_BG["plains"])
        def_col = FIELD_BG.get(b.defender_terrain, FIELD_BG["plains"])
        pygame.draw.rect(
            surface, atk_col,
            (self.ox, self.oy, mid - self.ox, self.cell * b.field_h),
        )
        pygame.draw.rect(
            surface, def_col,
            (mid, self.oy, self.ox + self.cell * b.field_w - mid, self.cell * b.field_h),
        )

    def _draw_walls(self, surface: pygame.Surface) -> None:
        if self.has_bg:
            return  # la muralla y la puerta ya están en el dibujo de fondo
        for (cx, cy) in self.battle.walls:
            px, py = self.to_px(cx, cy)
            rect = pygame.Rect(px, py, int(self.cell) + 1, int(self.cell) + 1)
            pygame.draw.rect(surface, WALL_COLOR, rect)
            pygame.draw.rect(surface, WALL_TOP, rect, 2)
        for (cx, cy) in self.battle.gate_cells:
            px, py = self.to_px(cx, cy)
            pygame.draw.rect(
                surface, GATE_COLOR, (px, py, int(self.cell) + 1, int(self.cell) + 1)
            )

    def _draw_units(self, surface: pygame.Surface) -> None:
        base_r = self.cell * DISC_SCALE
        # Pintar de atrás (arriba/lejos) hacia adelante (abajo/cerca): las
        # cercanas tapan a las lejanas, reforzando la profundidad.
        units = sorted(
            (u for u in self.battle.units if u.active), key=lambda u: u.y
        )
        for u in units:
            depth = self._depth(u.y)
            r = max(6, int(base_r * depth))
            ring = max(2, int(self.cell * 0.12 * depth))
            px, py = self.to_px(u.x, u.y)
            color = theme.player_color(u.owner)  # color del jugador dueño
            # Disco translúcido y claro (deja ver la pradera por la transparencia
            # del sprite) + sprite + anillo opaco del dueño para leer el bando.
            self._blit_disc(surface, px, py, r, _lighten(color))
            sprite = self._sprite(u.class_id, depth)
            surface.blit(sprite, sprite.get_rect(center=(px, py)))
            pygame.draw.circle(surface, color, (px, py), r, ring)
            if u.id in self.selected:
                pygame.draw.circle(surface, SELECT_COLOR, (px, py), r + ring, 2)
            self._draw_health(surface, u, px, py, r)
            self._draw_count(surface, u, px, py, r)

    def _blit_disc(self, surface, px, py, r, color) -> None:
        """Disco translúcido del color del dueño (deja ver el fondo detrás)."""
        disc = pygame.Surface((2 * r, 2 * r), pygame.SRCALPHA)
        pygame.draw.circle(disc, (*color, DISC_ALPHA), (r, r), r)
        surface.blit(disc, (px - r, py - r))
        # Líneas de ataque de las fichas seleccionadas (feedback de órdenes).
        for uid in self.selected:
            u = self.battle.unit_by_id(uid)
            if u is None or u.order is None or u.order[0] != "attack":
                continue
            t = self.battle.unit_by_id(u.order[1])
            if t is not None and t.active:
                pygame.draw.line(surface, ATTACK_LINE, self.to_px(u.x, u.y), self.to_px(t.x, t.y), 1)

    def _draw_count(self, surface, u: Unit, px: int, py: int, r: int) -> None:
        count = self.count_font.render(str(u.troops), True, theme.TEXT)
        shadow = self.count_font.render(str(u.troops), True, (0, 0, 0))
        pos = (px + r - count.get_width(), py + r - count.get_height())
        surface.blit(shadow, (pos[0] + 1, pos[1] + 1))
        surface.blit(count, pos)

    def _draw_arrows(self, surface: pygame.Surface) -> None:
        """Flechas de los arqueros en vuelo (efecto visual del disparo)."""
        for arrow in self._arrows:
            t = min(1.0, arrow["t"] / arrow["dur"])
            fx, fy = arrow["from"]
            tx, ty = arrow["to"]
            # Cabeza de la flecha viajando del arquero al objetivo; cola atrás.
            hx = fx + (tx - fx) * t
            hy = fy + (ty - fy) * t
            back = max(0.0, t - 0.18)
            bx = fx + (tx - fx) * back
            by = fy + (ty - fy) * back
            pygame.draw.line(
                surface, (245, 240, 210), self.to_px(bx, by), self.to_px(hx, hy),
                max(1, int(self.cell * 0.06)),
            )

    def _draw_health(self, surface, u: Unit, px: int, py: int, r: int) -> None:
        frac = max(0.0, min(1.0, u.hp / u.max_hp)) if u.max_hp else 0.0
        w = r * 2
        bar = pygame.Rect(px - r, py - r - 7, w, 4)
        pygame.draw.rect(surface, (40, 40, 40), bar)
        pygame.draw.rect(surface, (80, 200, 90), (bar.x, bar.y, int(w * frac), 4))

    def _draw_drag_box(self, surface: pygame.Surface) -> None:
        if self._drag_start is None or self._drag_now is None:
            return
        x0, y0 = self._drag_start
        x1, y1 = self._drag_now
        box = pygame.Rect(min(x0, x1), min(y0, y1), abs(x1 - x0), abs(y1 - y0))
        pygame.draw.rect(surface, SELECT_COLOR, box, 1)

    def _draw_hud(self, surface: pygame.Surface) -> None:
        pygame.draw.rect(surface, (20, 22, 26), (0, 0, self.win_w, HUD_H))
        b = self.battle
        atk = b.side_troops(b.attacker_owner)
        dfn = b.side_troops(b.defender_owner)
        mine = atk if self.human_owner == b.attacker_owner else dfn
        theirs = dfn if self.human_owner == b.attacker_owner else atk
        spectator = self.net is not None and not self.participant
        if spectator:
            label = f"Atacante: {atk}    Defensor: {dfn}    Tiempo: {int(b.elapsed)}s"
        else:
            label = f"Tus tropas: {mine}    Enemigo: {theirs}    Tiempo: {int(b.elapsed)}s"
        txt = self.font.render(label, True, theme.TEXT)
        surface.blit(txt, (20, HUD_H // 2 - txt.get_height() // 2))
        ready_word = "listo" if self.net is not None else "comenzar"
        if spectator:
            hint_text = "Mirando la batalla — la dirigen los jugadores en combate"
        elif self.phase == "planning":
            hint_text = (
                "1-4: formación (línea/clásica/compacta/en V)"
                f" · clic: seleccionar · clic der.: mover · Espacio: {ready_word}"
            )
        else:
            hint_text = (
                "Clic: una unidad · arrastrá: grupo · clic der.: mover/atacar"
                " · H: aguantar · A: todas"
            )
            if self.net is None:
                hint_text += " · ESC: pausa"
        hint = self.small_font.render(hint_text, True, theme.TEXT_DIM)
        surface.blit(hint, (20, HUD_H - hint.get_height() - 4))
        mouse = scale.mouse_pos()
        if self.phase == "planning" and not spectator:
            start_label = "Listo (Espacio)" if self.net is not None else "Comenzar (Espacio)"
            self._draw_button(surface, self._start_btn, start_label, mouse, primary=True)
        if self.net is None:  # auto-resolver solo en single-player
            self._draw_button(surface, self._auto_btn, "Auto-resolver", mouse)

    def _draw_button(self, surface, rect, text, mouse, *, primary=False) -> None:
        if primary:
            base, hover = (70, 130, 70), (95, 165, 95)
        else:
            base, hover = theme.BUTTON_BG, theme.BUTTON_BG_OVER
        color = hover if rect.collidepoint(mouse) else base
        pygame.draw.rect(surface, color, rect, border_radius=6)
        label = self.font.render(text, True, theme.TEXT)
        surface.blit(label, label.get_rect(center=rect.center))

    def _draw_result(self, surface: pygame.Surface) -> None:
        outcome = self.result.outcome
        won = (
            outcome == BattleOutcome.ATTACKER_WINS
            and self.human_owner == self.battle.attacker_owner
        ) or (
            outcome == BattleOutcome.DEFENDER_WINS
            and self.human_owner == self.battle.defender_owner
        )
        title = OUTCOME_TEXT.get(outcome, "Batalla terminada")
        self._draw_center_banner(
            surface, title, subtitle="Clic para continuar",
            color=(120, 220, 120) if won else theme.TEXT,
        )

    def _draw_countdown(self, surface: pygame.Surface) -> None:
        """Cuenta regresiva de preparación: panel chico arriba al centro, sin
        oscurecer el campo para que se vean bien las tropas. Incluye el selector
        de formación (1-4), con la activa resaltada."""
        secs = max(0, int(math.ceil(self._countdown)))
        panel = pygame.Rect(0, 0, 640, 134)
        panel.midtop = (self.win_w // 2, HUD_H + 12)
        bg = pygame.Surface(panel.size, pygame.SRCALPHA)
        bg.fill((0, 0, 0, 140))
        surface.blit(bg, panel.topleft)
        pygame.draw.rect(surface, SELECT_COLOR, panel, 2, border_radius=10)
        head = self.font.render("Preparación — ordená a tus tropas", True, theme.TEXT)
        surface.blit(head, head.get_rect(midtop=(panel.centerx, panel.y + 8)))
        big = self.title_font.render(f"Comienza en {secs}…", True, SELECT_COLOR)
        surface.blit(big, big.get_rect(midtop=(panel.centerx, panel.y + 36)))
        self._draw_formation_chooser(surface)

    def _draw_formation_chooser(self, surface) -> None:
        """Fila de opciones de formación (1-4); la activa va resaltada. Cada
        etiqueta es clickeable (rects calculados en `_layout_formation_chooser`)."""
        for key, (rect, text) in self._formation_rects.items():
            active = key == self.formation
            if active:
                pygame.draw.rect(surface, SELECT_COLOR, rect.inflate(10, 6), border_radius=5)
            label = self.small_font.render(
                text, True, (20, 20, 20) if active else theme.TEXT
            )
            surface.blit(label, rect)

    def _draw_center_banner(self, surface, text, subtitle=None, color=theme.TEXT) -> None:
        overlay = pygame.Surface((self.win_w, self.win_h), pygame.SRCALPHA)
        overlay.fill((0, 0, 0, 150))
        surface.blit(overlay, (0, 0))
        t = self.title_font.render(text, True, color)
        surface.blit(t, t.get_rect(center=(self.win_w // 2, self.win_h // 2 - 20)))
        if subtitle:
            s = self.font.render(subtitle, True, theme.TEXT_DIM)
            surface.blit(s, s.get_rect(center=(self.win_w // 2, self.win_h // 2 + 30)))
