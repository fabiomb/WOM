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
                  fuerte (no salen al mapa solas). El orden entre los fuertes
                  de un jugador rota por turno, así los capturados también
                  producen cuando la comida escasea.
6. Recuperación — XP según ubicación, comida de los ejércitos; un ejército
                  en fuerte propio se reabastece de la reserva hasta
                  max_army_size y come del stock; en pueblo propio come
                  directo del pueblo (sin stock); los ejércitos con XP <= 0
                  o sin tropas se eliminan (cruz en el mapa).
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
    # Nivel de la AI (facil/medio/dificil) si is_ai; lo persiste el savegame
    # para reconstruir el AIPlayer al cargar la partida.
    ai_level: str | None = None
    # Tropas propias perdidas en toda la partida (bajas en batalla y
    # ejércitos disueltos); lo muestra el HUD.
    troops_lost: int = 0

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "is_ai": self.is_ai,
            "food": self.food,
            "ai_level": self.ai_level,
            "troops_lost": self.troops_lost,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Player":
        return cls(**data)


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
    # Cruces de ejércitos muertos: (x, y, turno de la muerte). La UI las
    # desvanece con la edad y el core las purga tras `turnos_cruz` turnos.
    crosses: list[tuple[int, int, int]] = field(default_factory=list)
    next_army_id: int = 0
    # Batallas del último turno: (posición del defensor, nombre del resultado).
    # Lo consumen la UI (feedback al jugador) y el debug de la AI.
    last_battles: list[tuple[Coord, str]] = field(default_factory=list)
    # Recorrido del último turno: army_id → tiles pisados en orden (posición
    # inicial primero, retirada incluida). Lo consume la UI para animar el
    # movimiento; transitorio como last_battles, no se serializa.
    last_moves: dict[int, list[Coord]] = field(default_factory=dict)
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
        self.last_moves.clear()
        self._apply_orders(orders)
        self._move_armies()
        self._resolve_battles()
        self._capture()
        self._produce()
        self._recover()
        self.turn += 1
        self._fade_crosses()
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
                self._record_move(army.id, army.position, step)
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
            self.players[attacker.owner].troops_lost += attacker.apply_losses(
                result.attacker_losses
            )
            self.players[defender.owner].troops_lost += defender.apply_losses(
                result.defender_losses
            )
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
        destination = self.rng.choice(
            [pos for pos in options if _manhattan(pos, enemy_pos) == far]
        )
        self._record_move(army.id, army.position, destination)
        army.position = destination

    def _record_move(self, army_id: int, origin: Coord, dest: Coord) -> None:
        """Acumula el recorrido del turno (para la animación de la UI)."""
        self.last_moves.setdefault(army_id, [origin]).append(dest)

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
        for player in self.players:
            forts = [
                f for f in self.world.forts
                if f.owner == player.id and not self._enemy_on(f.position, player.id)
            ]
            if not forts:
                continue
            # El orden rota por turno: si la comida no alcanza para todos,
            # cada fuerte (incluidos los capturados) produce a su turno en
            # vez de que el primero de la lista acapare todo el stock.
            start = self.turn % len(forts)
            for fort in forts[start:] + forts[:start]:
                troops = min(int(player.food * rate), max_reserve - fort.reserve_total)
                if troops <= 0:
                    continue
                self._add_to_reserve(fort, troops)
                player.food = max(0, player.food - math.ceil(troops / rate))

    def _enemy_on(self, pos: Coord, player_id: int) -> bool:
        """True si un ejército enemigo está pisando el tile (bloquea producción)."""
        occupant = self.army_at(pos)
        return occupant is not None and occupant.owner != player_id

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
                    # Un pueblo propio alimenta al ejército directamente, sin
                    # depender del stock (que los fuertes suelen agotar).
                    army.food = min(100, army.food + food_cfg["refuerzo_en_base"])
            else:
                army.xp = min(xp_max, army.xp + xp_cfg["campo"])
                army.food = max(0, army.food - food_cfg["decaimiento_por_turno"])

    def _kill(self, army: Army) -> None:
        """Elimina un ejército derrotado y deja una cruz en el mapa."""
        # Las tropas que quedaban (p. ej. al morir por XP) también cuentan
        # como perdidas.
        self.players[army.owner].troops_lost += army.total_troops
        self.crosses.append((*army.position, self.turn))
        self.armies.remove(army)

    def _fade_crosses(self) -> None:
        """Purga las cruces que ya cumplieron su vida útil (turnos_cruz)."""
        lifetime = self.config["turnos_cruz"]
        self.crosses = [c for c in self.crosses if self.turn - c[2] <= lifetime]

    # --- consultas y helpers usados por UI, AI y persistencia -------------

    def merge_armies(self, source_id: int, target_id: int) -> bool:
        """Fusiona el ejército `source` dentro de `target` (acción del jugador,
        fuera de las fases del turno).

        Requiere mismo dueño, tiles aledaños y que la suma no supere
        max_army_size. El target absorbe la composición; XP y comida quedan
        como promedio ponderado por tropas. El source desaparece sin dejar
        cruz (no murió: se integró). Devuelve False si no se pudo.
        """
        source = self.army_by_id(source_id)
        target = self.army_by_id(target_id)
        if source is None or target is None or source is target:
            return False
        if source.owner != target.owner:
            return False
        if not _adjacent(source.position, target.position):
            return False
        total = source.total_troops + target.total_troops
        if total == 0 or total > self.config["max_army_size"]:
            return False
        target.xp = round(
            (target.xp * target.total_troops + source.xp * source.total_troops) / total
        )
        target.food = round(
            (target.food * target.total_troops + source.food * source.total_troops) / total
        )
        for class_id, count in source.composition.items():
            target.composition[class_id] = target.composition.get(class_id, 0) + count
        self.armies.remove(source)
        return True

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

    def to_dict(self) -> dict:
        """Estado completo serializable a JSON (savegames).

        Incluye el estado interno del RNG: una partida cargada continúa
        exactamente igual que la original (mismo mapa, mismas batallas).
        `last_battles` y `_battle_queue` son transitorios y no se guardan.
        """
        return {
            "seed": self.seed,
            "turn": self.turn,
            "victory_mode": self.victory_mode.value,
            "world": self.world.to_dict(),
            "players": [p.to_dict() for p in self.players],
            "armies": [a.to_dict() for a in self.armies],
            "crosses": [list(c) for c in self.crosses],
            "next_army_id": self.next_army_id,
            "rng_state": _encode_rng_state(self.rng.getstate()),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Game":
        """Reconstruye una partida guardada; la config se relee de data/."""
        rng = random.Random()
        rng.setstate(_decode_rng_state(data["rng_state"]))
        return cls(
            world=WorldMap.from_dict(data["world"]),
            players=[Player.from_dict(p) for p in data["players"]],
            armies=[Army.from_dict(a) for a in data["armies"]],
            classes=load_unit_classes(),
            config=load_game_config(),
            victory_mode=VictoryMode(data["victory_mode"]),
            rng=rng,
            seed=data["seed"],
            turn=data["turn"],
            # Compat con saves previos a las cruces con edad ([x, y]): se les
            # asigna el turno actual y empiezan a desvanecerse desde ahora.
            crosses=[
                tuple(c) if len(c) == 3 else (c[0], c[1], data["turn"])
                for c in data["crosses"]
            ],
            next_army_id=data["next_army_id"],
        )

    def armies_of(self, player_id: int) -> list[Army]:
        return [a for a in self.armies if a.owner == player_id]

    def army_by_id(self, army_id: int) -> Army | None:
        return next((a for a in self.armies if a.id == army_id), None)

    def army_at(self, pos: Coord) -> Army | None:
        return next((a for a in self.armies if a.position == pos), None)


def _encode_rng_state(state: tuple) -> list:
    """random.Random.getstate() → estructura JSON: (versión, ints, gauss)."""
    version, internal, gauss_next = state
    return [version, list(internal), gauss_next]


def _decode_rng_state(data: list) -> tuple:
    version, internal, gauss_next = data
    return (version, tuple(internal), gauss_next)


def _adjacent(a: Coord, b: Coord) -> bool:
    return abs(a[0] - b[0]) + abs(a[1] - b[1]) == 1


def _manhattan(a: Coord, b: Coord) -> int:
    return abs(a[0] - b[0]) + abs(a[1] - b[1])
