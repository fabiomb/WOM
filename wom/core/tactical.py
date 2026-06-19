"""Combate táctico en tiempo real (campo abierto y asalto a fuerte).

Es una **capa de UI**: NO interviene en el camino determinista de
`Game.run_turn` (red/headless/IA siguen usando `resolve_battle`). En partida
individual el jugador puede elegir dirigir la batalla acá; cuando termina,
`to_battle_result()` traduce las bajas a un `BattleResult` que el core aplica
con su maquinaria habitual (`resolve_one_battle`), así el core sigue siendo la
autoridad y el zoom solo *elige el resultado* en lugar del RNG.

Modelo: cada ejército se descompone en **fichas** (cada una representa varias
tropas de una clase, con `troops` exactos para que el mapeo de bajas sea
preciso). Las fichas se mueven y atacan en tiempo real sobre un campo propio
(grilla flotante). Los stats salen de `classes.json` (velocidad → velocidad de
desplazamiento, ataque → daño, defensa → resistencia, `bonus_vs`/`bonus_terreno`
→ multiplicadores) y se modulan por la comida/XP del ejército igual que la
fórmula de `battle.py`. En el escenario de **fuerte** el defensor está
amurallado y el atacante debe cruzar la puerta; los arqueros (`ignora_bonus_fort`)
disparan por encima de la muralla y anulan su bonus defensivo.

`step(dt)` avanza la simulación; cuando un bando muere o se quiebra (cae por
debajo de `umbral_quiebre`) la batalla termina. El defensor dentro de un fuerte
no se quiebra: aguanta hasta morir (igual que la retirada del core).

Sin pygame a propósito (`test_smoke`): es matemática pura, con RNG seedeado
para reproducibilidad y tests. El render y el input viven en `wom/ui`.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field

from wom.core.army import Army
from wom.core.battle import (
    BattleOutcome,
    BattleResult,
    _classify,
    _xp_deltas,
)
from wom.core.config import UnitClass
from wom.core.worldmap import WorldMap


# --- órdenes del jugador para un grupo de fichas -------------------------
# None ⇒ comportamiento automático (buscar al enemigo más cercano).
OrderKind = str  # "move" | "attack" | "hold"


@dataclass
class Unit:
    """Una ficha del combate: representa `troops` tropas de una clase."""

    id: int
    owner: int
    class_id: str
    troops0: int          # tropas iniciales que representa (para mapear bajas)
    hp: float             # vida actual; 1 hp = 1 tropa
    max_hp: float
    x: float
    y: float
    cooldown: float = 0.0
    target_id: int | None = None
    fled: bool = False     # abandonó el campo al quebrarse su bando
    order: tuple | None = None  # ("move", x, y) | ("attack", id) | ("hold",)

    @property
    def troops(self) -> int:
        """Tropas vivas que representa (la vida redondeada)."""
        return max(0, round(self.hp))

    @property
    def dead(self) -> bool:
        return self.hp <= 0

    @property
    def active(self) -> bool:
        """Sigue peleando en el campo (ni muerta ni huida)."""
        return self.hp > 0 and not self.fled


class TacticalBattle:
    """Simulación en tiempo real de una batalla entre dos ejércitos."""

    def __init__(
        self,
        *,
        attacker_owner: int,
        defender_owner: int,
        units: list[Unit],
        classes: dict[str, UnitClass],
        battle_config: dict,
        attacker_terrain: str,
        defender_terrain: str,
        defender_in_fort: bool,
        defender_in_town: bool,
        field_w: int,
        field_h: int,
        walls: frozenset[tuple[int, int]] = frozenset(),
        gate_cells: tuple[tuple[int, int], ...] = (),
        rng: random.Random | None = None,
    ):
        self.attacker_owner = attacker_owner
        self.defender_owner = defender_owner
        self.units = units
        self.classes = classes
        self.cfg = battle_config
        self.tcfg = battle_config["tactico"]
        self.attacker_terrain = attacker_terrain
        self.defender_terrain = defender_terrain
        self.defender_in_fort = defender_in_fort
        self.defender_in_town = defender_in_town
        self.field_w = field_w
        self.field_h = field_h
        self.walls = walls
        self.gate_cells = gate_cells
        self.rng = rng or random.Random()

        self.elapsed = 0.0
        self.over = False
        self.attack_events: list[tuple[int, int]] = []  # (atacante, objetivo) este step

        # Factores de daño por bando (comida/XP, igual que _army_power de
        # battle.py): los setea build_tactical_battle desde los Army reales;
        # 1.0 (neutro) por defecto para construcción directa en tests.
        self._dmg_factor = {attacker_owner: 1.0, defender_owner: 1.0}
        self._troops0 = {
            attacker_owner: self._side_troops(attacker_owner, initial=True),
            defender_owner: self._side_troops(defender_owner, initial=True),
        }
        self._broken: set[int] = set()

    # --- consultas -------------------------------------------------------
    def _side_troops(self, owner: int, *, initial: bool = False) -> int:
        total = 0
        for u in self.units:
            if u.owner != owner:
                continue
            total += u.troops0 if initial else (u.troops if not u.fled else u.troops)
        return total

    def side_troops(self, owner: int) -> int:
        """Tropas vivas de un bando (incluye las que huyeron: sobreviven)."""
        return sum(u.troops for u in self.units if u.owner == owner and not u.dead)

    def is_attacker(self, owner: int) -> bool:
        return owner == self.attacker_owner

    def unit_by_id(self, uid: int) -> Unit | None:
        for u in self.units:
            if u.id == uid:
                return u
        return None

    # --- órdenes del jugador / IA ----------------------------------------
    def command(self, unit_ids, kind: OrderKind, target=None) -> None:
        """Asigna una orden a un grupo de fichas (las del jugador o la IA)."""
        for uid in unit_ids:
            u = self.unit_by_id(uid)
            if u is None or not u.active:
                continue
            if kind == "move":
                u.order = ("move", float(target[0]), float(target[1]))
            elif kind == "attack":
                u.order = ("attack", int(target))
            elif kind == "hold":
                u.order = ("hold",)
            else:
                u.order = None

    # --- paso de simulación ----------------------------------------------
    def step(self, dt: float) -> None:
        if self.over:
            return
        self._last_dt = dt
        self.attack_events.clear()
        self.elapsed += dt
        self._update_break_state()
        for u in self.units:
            if not u.active:
                continue
            u.cooldown = max(0.0, u.cooldown - dt)
            self._update_unit(u, dt)
        self._check_over()

    def _update_break_state(self) -> None:
        """Marca bandos quebrados (el defensor en fuerte nunca se quiebra)."""
        umbral = self.tcfg["umbral_quiebre"]
        for owner in (self.attacker_owner, self.defender_owner):
            if owner in self._broken:
                continue
            if owner == self.defender_owner and self.defender_in_fort:
                continue  # atrincherado: pelea hasta morir
            init = max(1, self._troops0[owner])
            if self.side_troops(owner) / init <= umbral:
                self._broken.add(owner)

    def _update_unit(self, u: Unit, dt: float) -> None:
        broken = u.owner in self._broken
        if broken:
            self._flee(u, dt)
            return
        target = self._resolve_target(u)
        if target is None:
            return
        rng_atk = self._attack_range(u)
        dist = math.hypot(target.x - u.x, target.y - u.y)
        if dist <= rng_atk and self._can_hit(u, target):
            self._attack(u, target)
        else:
            self._advance_toward(u, target.x, target.y, dt)

    def _resolve_target(self, u: Unit) -> Unit | None:
        order = u.order
        if order is not None:
            if order[0] == "hold":
                return self._nearest_enemy(u, only_in_range=True)
            if order[0] == "attack":
                t = self.unit_by_id(order[1])
                if t is not None and t.active:
                    return t
                u.order = None  # objetivo muerto: vuelve a automático
            if order is not None and order[0] == "move":
                # En tránsito hacia un punto; pelea sólo si tiene a tiro.
                return self._move_or_fight(u)
        return self._nearest_enemy(u)

    def _move_or_fight(self, u: Unit) -> Unit | None:
        _, tx, ty = u.order
        if math.hypot(tx - u.x, ty - u.y) <= 0.4:
            u.order = None  # llegó: retoma automático
            return self._nearest_enemy(u)
        enemy = self._nearest_enemy(u, only_in_range=True)
        if enemy is not None:
            return enemy
        self._advance_toward(u, tx, ty, _MOVE_DT_SENTINEL)
        return None

    def _nearest_enemy(self, u: Unit, *, only_in_range: bool = False) -> Unit | None:
        best, best_d = None, math.inf
        rng_atk = self._attack_range(u)
        for other in self.units:
            if other.owner == u.owner or not other.active:
                continue
            d = math.hypot(other.x - u.x, other.y - u.y)
            if only_in_range and d > rng_atk:
                continue
            if d < best_d:
                best, best_d = other, d
        return best

    def _attack_range(self, u: Unit) -> float:
        if self.classes[u.class_id].ignora_bonus_fort:  # arquero: a distancia
            return self.tcfg["alcance_arquero"]
        return self.tcfg["alcance_cuerpo"]

    def _can_hit(self, u: Unit, target: Unit) -> bool:
        """El cuerpo a cuerpo no atraviesa muros; el arquero dispara por encima."""
        if self.classes[u.class_id].ignora_bonus_fort:
            return True
        return not self._segment_blocked(u.x, u.y, target.x, target.y)

    def _attack(self, u: Unit, target: Unit) -> None:
        if u.cooldown > 0:
            return
        u.cooldown = self.tcfg["cooldown_ataque"]
        unit = self.classes[u.class_id]
        terrain = (
            self.attacker_terrain if self.is_attacker(u.owner) else self.defender_terrain
        )
        bonus_terr = unit.bonus_terreno.get(terrain, 1.0)
        bonus_vs = unit.bonus_vs.get(target.class_id, 1.0)
        dps = (
            u.troops
            * unit.ataque
            * bonus_vs
            * bonus_terr
            * self._dmg_factor.get(u.owner, 1.0)
            * self.tcfg["escala_dano"]
        )
        # Resistencia del objetivo: defensa * terreno * fuerte/pueblo.
        tgt_unit = self.classes[target.class_id]
        tgt_terrain = (
            self.attacker_terrain if self.is_attacker(target.owner) else self.defender_terrain
        )
        resist = max(1.0, tgt_unit.defensa) * tgt_unit.bonus_terreno.get(tgt_terrain, 1.0)
        if target.owner == self.defender_owner and not unit.ignora_bonus_fort:
            if self.defender_in_fort:
                resist *= self.cfg["bonus_defensa_fort"]
            elif self.defender_in_town:
                resist *= self.cfg["bonus_defensa_town"]
        damage = dps * self.tcfg["cooldown_ataque"] / resist
        target.hp -= damage
        self.attack_events.append((u.id, target.id))
        if target.hp <= 0 and target.target_id == u.id:
            target.target_id = None

    def _flee(self, u: Unit, dt: float) -> None:
        """Bando quebrado: corre hacia su borde y abandona el campo."""
        edge_x = -2.0 if self.is_attacker(u.owner) else self.field_w + 2.0
        self._advance_toward(u, edge_x, u.y, dt, avoid_walls=False)
        if not (0.0 <= u.x <= self.field_w):
            u.fled = True

    def _advance_toward(
        self, u: Unit, tx: float, ty: float, dt: float, *, avoid_walls: bool = True
    ) -> None:
        if dt is _MOVE_DT_SENTINEL:
            dt = self._last_dt
        speed = self.classes[u.class_id].velocidad * self.tcfg["escala_velocidad"]
        dx, dy = tx - u.x, ty - u.y
        norm = math.hypot(dx, dy) or 1.0
        vx, vy = dx / norm, dy / norm
        # Si hay muralla y el destino está del otro lado, encaminar por la puerta.
        if avoid_walls and self.walls and self._needs_gate(u, tx, ty):
            gx, gy = self._nearest_gate(u)
            gdx, gdy = gx - u.x, gy - u.y
            gnorm = math.hypot(gdx, gdy) or 1.0
            vx, vy = gdx / gnorm, gdy / gnorm
        sx, sy = self._separation(u)
        vx += sx
        vy += sy
        step = speed * dt
        nx, ny = u.x + vx * step, u.y + vy * step
        nx, ny = self._slide(u.x, u.y, nx, ny, avoid_walls)
        u.x = min(max(nx, -2.0), self.field_w + 2.0)
        u.y = min(max(ny, 0.0), self.field_h)

    def _separation(self, u: Unit) -> tuple[float, float]:
        radius = self.tcfg["separacion_radio"]
        force = self.tcfg["separacion_fuerza"]
        px = py = 0.0
        for other in self.units:
            if other is u or not other.active or other.owner != u.owner:
                continue
            dx, dy = u.x - other.x, u.y - other.y
            d = math.hypot(dx, dy)
            if 0 < d < radius:
                push = (radius - d) / radius
                px += (dx / d) * push
                py += (dy / d) * push
        return px * force, py * force

    # --- muros / puerta ---------------------------------------------------
    def _is_wall(self, x: float, y: float) -> bool:
        return (int(x), int(y)) in self.walls

    def _slide(self, ox, oy, nx, ny, avoid_walls):
        if not avoid_walls or not self.walls:
            return nx, ny
        if not self._is_wall(nx, ny):
            return nx, ny
        if not self._is_wall(nx, oy):  # desliza en X
            return nx, oy
        if not self._is_wall(ox, ny):  # desliza en Y
            return ox, ny
        return ox, oy  # trabado

    def _inside_walls(self, x: float, y: float) -> bool:
        if not self.walls:
            return False
        xs = [c[0] for c in self.walls]
        ys = [c[1] for c in self.walls]
        return min(xs) < x < max(xs) and min(ys) < y < max(ys)

    def _needs_gate(self, u: Unit, tx: float, ty: float) -> bool:
        # Encaminar por la puerta sólo si cruzar de afuera hacia adentro.
        return self._inside_walls(tx, ty) and not self._inside_walls(u.x, u.y)

    def _nearest_gate(self, u: Unit) -> tuple[float, float]:
        best, best_d = (self.field_w / 2, self.field_h / 2), math.inf
        for gx, gy in self.gate_cells:
            d = math.hypot(gx + 0.5 - u.x, gy + 0.5 - u.y)
            if d < best_d:
                best, best_d = (gx + 0.5, gy + 0.5), d
        return best

    def _segment_blocked(self, x0, y0, x1, y1) -> bool:
        if not self.walls:
            return False
        steps = int(math.hypot(x1 - x0, y1 - y0) * 2) + 1
        for i in range(1, steps):
            t = i / steps
            if self._is_wall(x0 + (x1 - x0) * t, y0 + (y1 - y0) * t):
                return True
        return False

    # --- fin de la batalla -----------------------------------------------
    def _check_over(self) -> None:
        atk = self.side_active(self.attacker_owner)
        dfn = self.side_active(self.defender_owner)
        if atk == 0 or dfn == 0 or self.elapsed >= self.tcfg["duracion_max"]:
            self.over = True

    def side_active(self, owner: int) -> int:
        """Tropas que siguen en el campo peleando (sin muertas ni huidas)."""
        return sum(u.troops for u in self.units if u.owner == owner and u.active)

    @property
    def finished(self) -> bool:
        return self.over

    def outcome(self) -> BattleOutcome:
        atk_alive = self.side_troops(self.attacker_owner)
        def_alive = self.side_troops(self.defender_owner)
        atk_broke = self.attacker_owner in self._broken
        def_broke = self.defender_owner in self._broken
        if def_alive == 0 and atk_alive > 0:
            return BattleOutcome.ATTACKER_WINS
        if atk_alive == 0 and def_alive > 0:
            return BattleOutcome.DEFENDER_WINS
        if atk_alive == 0 and def_alive == 0:
            return BattleOutcome.DRAW
        if atk_broke:
            return BattleOutcome.ATTACKER_RETREATS
        if def_broke:
            return BattleOutcome.DEFENDER_RETREATS
        # Corte por tiempo con ambos vivos: clasifica por ratio de tropas.
        ratio = atk_alive / max(def_alive, 1e-9)
        return _classify(ratio, self.cfg)

    def to_battle_result(self) -> BattleResult:
        """Traduce el estado final a un BattleResult que el core aplica."""
        outcome = self.outcome()
        attacker_losses = self._losses(self.attacker_owner)
        defender_losses = self._losses(self.defender_owner)
        attacker_xp, defender_xp = _xp_deltas(outcome, self.cfg)
        return BattleResult(
            outcome=outcome,
            attacker_losses=attacker_losses,
            defender_losses=defender_losses,
            attacker_xp_delta=attacker_xp,
            defender_xp_delta=defender_xp,
        )

    def _losses(self, owner: int) -> dict[str, int]:
        initial: dict[str, int] = {}
        survivors: dict[str, int] = {}
        for u in self.units:
            if u.owner != owner:
                continue
            initial[u.class_id] = initial.get(u.class_id, 0) + u.troops0
            survivors[u.class_id] = survivors.get(u.class_id, 0) + u.troops
        return {
            cid: max(0, initial[cid] - survivors.get(cid, 0))
            for cid in initial
            if initial[cid] - survivors.get(cid, 0) > 0
        }


# Centinela para reutilizar el último dt dentro de _move_or_fight.
_MOVE_DT_SENTINEL = object()
# Atributo de instancia con el dt del paso actual (lo setea step indirectamente).
TacticalBattle._last_dt = 1 / 60  # type: ignore[attr-defined]


def build_tactical_battle(
    attacker: Army,
    defender: Army,
    world: WorldMap,
    classes: dict[str, UnitClass],
    battle_config: dict,
    rng: random.Random,
) -> TacticalBattle:
    """Arma una TacticalBattle a partir de los dos ejércitos del core.

    Lee el terreno de cada bando, si el defensor está en fuerte/pueblo y, en
    ese caso, monta una muralla rectangular con una puerta del lado del
    atacante. Despliega las fichas (cada una con tropas exactas para mapear
    bajas) en zonas opuestas del campo.
    """
    tcfg = battle_config["tactico"]
    w, h = tcfg["campo_ancho"], tcfg["campo_alto"]
    defender_in_fort = world.fort_at(defender.position) is not None
    defender_in_town = world.town_at(defender.position) is not None

    walls: set[tuple[int, int]] = set()
    gate: list[tuple[int, int]] = []
    if defender_in_fort:
        walls, gate = _castle_layout(w, h)

    units: list[Unit] = []
    next_id = 0
    # Atacante a la izquierda; defensor a la derecha (o dentro del castillo).
    next_id = _deploy(
        attacker, classes, tcfg, rng, units, next_id,
        zone=(2.0, w * 0.28, 1.5, h - 1.5),
    )
    if defender_in_fort:
        inner = (min(c[0] for c in walls) + 1.5, max(c[0] for c in walls) - 0.5,
                 min(c[1] for c in walls) + 1.5, max(c[1] for c in walls) - 0.5)
        _deploy(defender, classes, tcfg, rng, units, next_id, zone=inner)
    else:
        _deploy(
            defender, classes, tcfg, rng, units, next_id,
            zone=(w * 0.72, w - 2.0, 1.5, h - 1.5),
        )

    battle = TacticalBattle(
        attacker_owner=attacker.owner,
        defender_owner=defender.owner,
        units=units,
        classes=classes,
        battle_config=battle_config,
        attacker_terrain=world.terrain_at(attacker.position).value,
        defender_terrain=world.terrain_at(defender.position).value,
        defender_in_fort=defender_in_fort,
        defender_in_town=defender_in_town,
        field_w=w,
        field_h=h,
        walls=frozenset(walls),
        gate_cells=tuple(gate),
        rng=rng,
    )
    # Factores de daño comida/XP reales de cada Army (igual que _army_power).
    battle._dmg_factor = {
        attacker.owner: _supply(attacker, battle_config),
        defender.owner: _supply(defender, battle_config),
    }
    return battle


def _supply(army: Army, cfg: dict) -> float:
    food_min = cfg["factor_comida_min"]
    xp_min = cfg["factor_xp_min"]
    f = food_min + (1.0 - food_min) * min(army.food, 100) / 100
    x = xp_min + (1.0 - xp_min) * min(army.xp, 100) / 100
    return f * x


def _deploy(army, classes, tcfg, rng, units, next_id, *, zone) -> int:
    """Crea fichas de un ejército dentro de `zone` (x0,x1,y0,y1)."""
    x0, x1, y0, y1 = zone
    total = army.total_troops
    if total <= 0:
        return next_id
    per_token = max(
        tcfg["tropas_por_ficha"],
        math.ceil(total / max(1, tcfg["max_fichas_por_bando"])),
    )
    for class_id, count in army.composition.items():
        if count <= 0:
            continue
        n_tokens = max(1, math.ceil(count / per_token))
        base = count // n_tokens
        extra = count - base * n_tokens
        for i in range(n_tokens):
            troops = base + (1 if i < extra else 0)
            if troops <= 0:
                continue
            x = rng.uniform(x0, x1)
            y = rng.uniform(y0, y1)
            units.append(
                Unit(
                    id=next_id,
                    owner=army.owner,
                    class_id=class_id,
                    troops0=troops,
                    hp=float(troops),
                    max_hp=float(troops),
                    x=x,
                    y=y,
                )
            )
            next_id += 1
    return next_id


def _castle_layout(w: int, h: int) -> tuple[set[tuple[int, int]], list[tuple[int, int]]]:
    """Muralla rectangular sobre la mitad derecha con una puerta a la izquierda."""
    x0, x1 = int(w * 0.62), w - 2
    y0, y1 = 2, h - 3
    walls: set[tuple[int, int]] = set()
    for x in range(x0, x1 + 1):
        walls.add((x, y0))
        walls.add((x, y1))
    for y in range(y0, y1 + 1):
        walls.add((x0, y))
        walls.add((x1, y))
    # Puerta: dos celdas en el centro del muro izquierdo (lado atacante).
    gate_y = (y0 + y1) // 2
    gate = [(x0, gate_y), (x0, gate_y + 1)]
    for cell in gate:
        walls.discard(cell)
    return walls, gate
