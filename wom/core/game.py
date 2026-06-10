"""Estado de la partida y motor de turnos.

Fases de un turno (orden fijo, determinista dada la seed):

1. Órdenes      — cada jugador asigna paths a sus ejércitos y/o crea
                  ejércitos desde la reserva de sus fuertes (UI o AI).
2. Movimiento   — avance según velocidad y costo de terreno, en orden de id
                  de ejército. Intentar entrar al tile de un enemigo detiene
                  a ambos y encola una batalla.
3. Batallas     — se resuelven todas las encoladas (core.battle). El bando
                  que pierde o se retira abandona su tile si sobrevive.
4. Captura      — fort/town con un ejército encima pasa a su dueño; la
                  reserva de un fuerte capturado se pierde.
5. Producción   — towns: +comida al stock del dueño; forts: producen tropas
                  según comida disponible que se ACUMULAN en la reserva del
                  fuerte (no salen al mapa solas).
6. Recuperación — XP según ubicación, comida de los ejércitos; un ejército
                  en fuerte propio se reabastece de la reserva hasta
                  max_army_size; los ejércitos con XP <= 0 o sin tropas se
                  eliminan (cruz en el mapa).
7. Victoria     — se evalúa la condición activa (core.victory).

Dos ejércitos nunca comparten tile (ni aliados, v1): un tile ocupado
detiene el movimiento.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field

from wom.core.army import Army
from wom.core.battle import (
    ATTACKER_FALLS_BACK,
    DEFENDER_FALLS_BACK,
    resolve_battle,
)
from wom.core.config import UnitClass, load_game_config, load_unit_classes
from wom.core.mapgen import MapParams, generate_map
from wom.core.orders import CreateArmyOrder, Order
from wom.core.victory import VictoryMode, VictoryResult, check_victory
from wom.core.worldmap import Coord, Fort, WorldMap


@dataclass
class Player:
    id: int
    name: str
    is_ai: bool = False
    food: int = 0  # stock de comida (producido por towns, consumido por forts)


@dataclass
class Game:
    """Estado completo de una partida. Serializable a savegame JSON."""

    world: WorldMap
    players: list[Player]
    armies: list[Army]
    classes: dict[str, UnitClass]
    config: dict
    victory_mode: VictoryMode
    rng: random.Random
    seed: int
    turn: int = 0
    crosses: list[Coord] = field(default_factory=list)  # ejércitos muertos
    next_army_id: int = 0
    # Batallas del último turno: (posición del defensor, nombre del resultado).
    # Lo consumen la UI (feedback al jugador) y el debug de la AI.
    last_battles: list[tuple[Coord, str]] = field(default_factory=list)
    _battle_queue: list[tuple[int, int]] = field(default_factory=list)

    @classmethod
    def new(
        cls,
        map_params: MapParams,
        players: list[Player],
        victory_mode: VictoryMode = VictoryMode.TOTAL,
    ) -> "Game":
        """Crea una partida nueva: genera el mapa y los ejércitos iniciales."""
        seed = map_params.seed if map_params.seed is not None else random.randrange(2**32)
        rng = random.Random(seed)
        world = generate_map(map_params, rng)
        game = cls(
            world=world,
            players=players,
            armies=[],
            classes=load_unit_classes(),
            config=load_game_config(),
            victory_mode=victory_mode,
            rng=rng,
            seed=seed,
        )
        for player in game.players:
            player.food = game.config["comida_inicial"]
        game._spawn_initial_armies()
        return game

    # --- fases del turno -------------------------------------------------

    def run_turn(self, orders: list[Order]) -> VictoryResult:
        """Ejecuta un turno completo y devuelve el estado de victoria."""
        self.last_battles.clear()
        self._apply_orders(orders)
        self._move_armies()
        self._resolve_battles()
        self._capture()
        self._produce()
        self._recover()
        self.turn += 1
        return check_victory(self, self.victory_mode)

    def _spawn_initial_armies(self) -> None:
        """Un ejército inicial (de config) en el fuerte de cada jugador."""
        initial = self.config["ejercito_inicial"]
        for fort in self.world.forts:
            if fort.owner >= 0:
                self.spawn_army(fort.owner, fort.position, dict(initial))

    def _apply_orders(self, orders: list[Order]) -> None:
        for order in orders:
            if isinstance(order, CreateArmyOrder):
                self._create_army_from_fort(order.position)
            else:
                army = self.army_by_id(order.army_id)
                if army is not None and not army.is_destroyed:
                    army.path = list(order.path)

    def _create_army_from_fort(self, position: Coord) -> None:
        """Ejecuta una CreateArmyOrder si sigue siendo válida."""
        fort = self.world.fort_at(position)
        if (
            fort is None
            or fort.owner < 0
            or fort.reserve_total <= 0
            or self.army_at(position) is not None
        ):
            return
        army = self.spawn_army(fort.owner, position, {})
        self._resupply(fort, army)

    def _move_armies(self) -> None:
        terrain_costs = self.config["costo_terreno"]
        for army in sorted(self.armies, key=lambda a: a.id):
            if army.is_destroyed or not army.path:
                continue
            points = army.speed(self.classes)
            while army.path and points > 0:
                step = army.path[0]
                if not self.world.is_passable(step) or not _adjacent(army.position, step):
                    army.path.clear()  # path inválido: se descarta
                    break
                cost = terrain_costs[self.world.terrain_at(step).value]
                if cost > points:
                    break  # sin puntos para este tile; sigue el próximo turno
                occupant = self.army_at(step)
                if occupant is not None:
                    if occupant.owner != army.owner:
                        self._battle_queue.append((army.id, occupant.id))
                        occupant.path.clear()
                    army.path.clear()  # tile ocupado (aliado o enemigo): se detiene
                    break
                army.position = step
                army.path.pop(0)
                points -= cost

    def _resolve_battles(self) -> None:
        for attacker_id, defender_id in self._battle_queue:
            attacker = self.army_by_id(attacker_id)
            defender = self.army_by_id(defender_id)
            if attacker is None or defender is None:
                continue  # destruido en una batalla previa de este turno
            result = resolve_battle(
                attacker, defender, self.world, self.classes,
                self.config["batalla"], self.rng,
            )
            self.last_battles.append((defender.position, result.outcome.name))
            attacker.apply_losses(result.attacker_losses)
            defender.apply_losses(result.defender_losses)
            attacker.xp += result.attacker_xp_delta
            defender.xp += result.defender_xp_delta
            if result.outcome in ATTACKER_FALLS_BACK:
                self._retreat(attacker, defender.position)
            if result.outcome in DEFENDER_FALLS_BACK:
                self._retreat(defender, attacker.position)
            for army in (attacker, defender):
                if army.is_destroyed:
                    self._kill(army)
        self._battle_queue.clear()

    def _retreat(self, army: Army, enemy_pos: Coord) -> None:
        """Mueve al ejército al tile vecino libre más alejado del enemigo."""
        if army.is_destroyed:
            return
        options = [
            pos for pos in self.world.neighbors(army.position) if self.army_at(pos) is None
        ]
        if not options:
            return  # rodeado: se queda y probablemente vuelva a pelear
        far = max(_manhattan(pos, enemy_pos) for pos in options)
        army.position = self.rng.choice(
            [pos for pos in options if _manhattan(pos, enemy_pos) == far]
        )

    def _capture(self) -> None:
        for site in (*self.world.forts, *self.world.towns):
            occupant = self.army_at(site.position)
            if occupant is not None and occupant.owner != site.owner:
                site.owner = occupant.owner
                if isinstance(site, Fort):
                    site.reserve = {}  # la guarnición en reserva se dispersa

    def _produce(self) -> None:
        for town in self.world.towns:
            if town.owner >= 0:
                self.players[town.owner].food += self.config["comida_por_town"]
        rate = self.config["tasa_produccion_fort"]
        max_reserve = self.config["max_reserva_fort"]
        for fort in self.world.forts:
            if fort.owner < 0:
                continue
            occupant = self.army_at(fort.position)
            if occupant is not None and occupant.owner != fort.owner:
                continue  # un enemigo está pisando el fuerte: no produce
            player = self.players[fort.owner]
            troops = min(int(player.food * rate), max_reserve - fort.reserve_total)
            if troops <= 0:
                continue
            self._add_to_reserve(fort, troops)
            player.food = max(0, player.food - math.ceil(troops / rate))

    def _add_to_reserve(self, fort: Fort, total: int) -> None:
        """Acumula tropas nuevas en la reserva, en partes iguales por clase."""
        class_ids = sorted(self.classes)
        per_class, remainder = divmod(total, len(class_ids))
        for i, class_id in enumerate(class_ids):
            extra = 1 if i < remainder else 0
            fort.reserve[class_id] = fort.reserve.get(class_id, 0) + per_class + extra

    def _resupply(self, fort: Fort, army: Army) -> None:
        """Transfiere tropas de la reserva al ejército hasta max_army_size.

        Reparte de a una por clase (round-robin) para mantener la
        composición balanceada.
        """
        deficit = self.config["max_army_size"] - army.total_troops
        class_ids = sorted(self.classes)
        while deficit > 0:
            transferred = False
            for class_id in class_ids:
                if deficit <= 0:
                    break
                if fort.reserve.get(class_id, 0) > 0:
                    fort.reserve[class_id] -= 1
                    army.composition[class_id] = army.composition.get(class_id, 0) + 1
                    deficit -= 1
                    transferred = True
            if not transferred:
                break  # reserva agotada

    def _recover(self) -> None:
        xp_cfg = self.config["xp_recuperacion"]
        food_cfg = self.config["comida_ejercito"]
        xp_max = self.config["xp_inicial"]
        for army in list(self.armies):
            if army.is_destroyed:  # XP en cero no se recupera: el ejército muere
                self._kill(army)
                continue
            site = self.world.fort_at(army.position) or self.world.town_at(army.position)
            own_site = site is not None and site.owner == army.owner
            if own_site:
                fort = self.world.fort_at(army.position)
                kind = "fort" if fort else "town"
                army.xp = min(xp_max, army.xp + xp_cfg[kind])
                if fort is not None:
                    self._resupply(fort, army)  # recarga tropas de la reserva
                player = self.players[army.owner]
                if player.food >= food_cfg["costo_stock_refuerzo"]:
                    player.food -= food_cfg["costo_stock_refuerzo"]
                    army.food = min(100, army.food + food_cfg["refuerzo_en_base"])
            else:
                army.xp = min(xp_max, army.xp + xp_cfg["campo"])
                army.food = max(0, army.food - food_cfg["decaimiento_por_turno"])

    def _kill(self, army: Army) -> None:
        """Elimina un ejército derrotado y deja una cruz en el mapa."""
        self.crosses.append(army.position)
        self.armies.remove(army)

    # --- consultas y helpers usados por UI, AI y persistencia -------------

    def spawn_army(self, owner: int, position: Coord, composition: dict[str, int]) -> Army:
        army = Army(
            id=self.next_army_id,
            owner=owner,
            position=position,
            composition=composition,
            xp=self.config["xp_inicial"],
        )
        self.next_army_id += 1
        self.armies.append(army)
        return army

    def armies_of(self, player_id: int) -> list[Army]:
        return [a for a in self.armies if a.owner == player_id]

    def army_by_id(self, army_id: int) -> Army | None:
        return next((a for a in self.armies if a.id == army_id), None)

    def army_at(self, pos: Coord) -> Army | None:
        return next((a for a in self.armies if a.position == pos), None)


def _adjacent(a: Coord, b: Coord) -> bool:
    return abs(a[0] - b[0]) + abs(a[1] - b[1]) == 1


def _manhattan(a: Coord, b: Coord) -> int:
    return abs(a[0] - b[0]) + abs(a[1] - b[1])
