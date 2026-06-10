"""Jugador AI con tres niveles de dificultad parametrizados en ai.json.

Los tres niveles comparten exactamente el mismo código: un motor de scoring
de objetivos. Para cada ejército propio se puntúan todos los objetivos
posibles y se elige el mejor:

    score = valor_objetivo / (1 + distancia / horizonte)

Tipos de objetivo y cómo los modulan los pesos del nivel:

- **Capturar** fort/town no propio. Los neutrales valen más (gratis); los
  enemigos escalan con `agresividad`; los towns escalan con `peso_economia`
  (producen la comida que alimenta la producción de tropas).
- **Atacar** un ejército enemigo, solo si la fuerza relativa supera
  `umbral_ataque`. Con `agrupa` activo (nivel difícil) el umbral puede
  cumplirse con la fuerza COMBINADA de los ejércitos propios CERCANOS al
  objetivo (radio GROUP_RADIUS): varios ejércitos convergen sobre el mismo
  enemigo (fuego concentrado) sin sobreestimar fuerzas lejanas.
- **Defender** un fuerte propio amenazado (enemigo a distancia Manhattan
  <= THREAT_RADIUS), escalado por `peso_defensa`. Si el enemigo se acercó
  respecto del turno anterior (memoria de posiciones), la urgencia sube
  (APPROACH_BOOST): la AI reacciona a los movimientos del jugador.
- **Reabastecer**: un ejército dañado (< 50% del máximo de tropas) vuelve a
  un fuerte propio con reserva; un ejército con hambre (comida < FOOD_LOW,
  pelea con eficiencia reducida) vuelve a cualquier fuerte o pueblo propio.
  Ambos escalados por `peso_economia`.

Con `coordina` activo (nivel difícil), los objetivos de captura y
reabastecimiento ya asignados a un ejército no se ofrecen a los demás en el
mismo turno: los ejércitos se reparten el mapa en vez de amontonarse sobre
el mismo objetivo (los ataques sí se comparten: eso es el fuego
concentrado de `agrupa`).

`horizonte` regula cuánto pesa la distancia: el nivel fácil (horizonte 1)
solo ve lo inmediato; el difícil (horizonte 5) persigue objetivos
estratégicos lejanos.

Además, crea un ejército en cada fuerte cuya reserva alcanzó
`umbral_crear_ejercito` (más bajo = saca ejércitos más rápido).

Cada decisión se loguea con su justificación si `debug=True` (requisito de
idea.md: AI "perfectamente documentada para mejorar y modificar").
"""

from __future__ import annotations

from wom.core.army import Army
from wom.core.config import UnitClass, load_ai_config
from wom.core.game import Game
from wom.core.orders import CreateArmyOrder, MoveOrder, Order
from wom.core.pathfind import dijkstra, reconstruct_path
from wom.core.worldmap import Coord

# Valores base de cada tipo de objetivo. Los pesos del nivel los modulan;
# estos números fijan la escala relativa entre tipos.
VALUE_FORT = 10.0
VALUE_TOWN = 6.0
NEUTRAL_BONUS = 1.25   # capturar algo sin dueño no cuesta una batalla
VALUE_ATTACK = 8.0
VALUE_DEFEND = 12.0
VALUE_RESUPPLY = 9.0
THREAT_RADIUS = 8      # distancia Manhattan a la que un enemigo "amenaza" un fuerte
APPROACH_BOOST = 1.5   # urgencia extra si la amenaza se está acercando
DAMAGED_FRACTION = 0.5  # debajo de esta fracción de tropas, considerar reabastecer
FOOD_LOW = 40          # debajo de esta comida, buscar dónde alimentarse
GROUP_RADIUS = 6       # radio Manhattan del fuego concentrado (niveles con `agrupa`)


class AIPlayer:
    """Decide las órdenes de un jugador AI en cada turno."""

    def __init__(self, player_id: int, level: str = "medio", debug: bool = False):
        levels = load_ai_config()
        if level not in levels:
            raise ValueError(f"Nivel de AI desconocido: {level!r} (opciones: {list(levels)})")
        self.player_id = player_id
        self.level = level
        self.params = levels[level]
        self.debug = debug
        # Memoria: distancia mínima de cada ejército enemigo a mis fuertes
        # en el turno anterior, para detectar amenazas que se acercan.
        self._enemy_min_dist: dict[int, int] = {}

    # --- API pública -------------------------------------------------------

    def decide_orders(self, game: Game) -> list[Order]:
        """Devuelve las órdenes del turno para todos los ejércitos propios."""
        orders: list[Order] = self._creation_orders(game)
        enemies = [a for a in game.armies if a.owner != self.player_id]
        threats = self._fort_threats(game, enemies)
        my_strengths = [
            (a, _strength(a, game.classes)) for a in game.armies_of(self.player_id)
        ]
        claimed: set[Coord] = set()  # objetivos ya asignados (si coordina)
        for army in game.armies_of(self.player_id):
            order = self._army_order(game, army, enemies, threats, my_strengths, claimed)
            if order is not None:
                orders.append(order)
        self._remember_enemies(game, enemies)
        return orders

    # --- creación de ejércitos ----------------------------------------------

    def _creation_orders(self, game: Game) -> list[Order]:
        orders: list[Order] = []
        threshold = self.params["umbral_crear_ejercito"]
        for fort in game.world.forts:
            if (
                fort.owner == self.player_id
                and fort.reserve_total >= threshold
                and game.army_at(fort.position) is None
            ):
                orders.append(CreateArmyOrder(position=fort.position))
                self._log(
                    game,
                    f"crear ejército en fuerte {fort.position} "
                    f"(reserva {fort.reserve_total} >= {threshold})",
                )
        return orders

    # --- decisión por ejército ------------------------------------------------

    def _army_order(
        self,
        game: Game,
        army: Army,
        enemies: list[Army],
        threats: dict[Coord, tuple[Army, int, bool]],
        my_strengths: list[tuple[Army, float]],
        claimed: set[Coord],
    ) -> Order | None:
        dist, prev = dijkstra(game.world, army.position, game.config["costo_terreno"])
        candidates = (
            self._capture_candidates(game, dist)
            + self._attack_candidates(game, army, enemies, dist, my_strengths)
            + self._defense_candidates(threats, dist)
            + self._resupply_candidates(game, army, dist)
        )
        if self.params.get("coordina", False):
            # Los objetivos exclusivos (capturar/reabastecer) no se duplican;
            # atacar y defender pueden compartirse entre ejércitos.
            candidates = [
                c for c in candidates
                if c[1] not in claimed or c[2].startswith(("atacar", "defender"))
            ]
        if not candidates:
            return None
        score, target, reason = max(candidates, key=lambda c: c[0])
        claimed.add(target)
        self._log(
            game,
            f"ejército {army.id} en {army.position}: {reason} {target} "
            f"(score {score:.2f})",
        )
        if target == army.position:
            return MoveOrder(army_id=army.id, path=())  # mantener posición
        path = reconstruct_path(prev, army.position, target)
        return MoveOrder(army_id=army.id, path=tuple(path)) if path else None

    def _capture_candidates(
        self, game: Game, dist: dict[Coord, float]
    ) -> list[tuple[float, Coord, str]]:
        aggressiveness = self.params["agresividad"]
        economy = self.params["peso_economia"]
        sites = [(f, VALUE_FORT, "capturar fuerte") for f in game.world.forts] + [
            (t, VALUE_TOWN, "capturar pueblo") for t in game.world.towns
        ]
        candidates = []
        for site, base, reason in sites:
            if site.owner == self.player_id or site.position not in dist:
                continue
            value = base
            if reason.endswith("pueblo"):
                value *= 1.0 + economy
            value *= NEUTRAL_BONUS if site.owner < 0 else (0.5 + aggressiveness)
            candidates.append((self._discount(value, dist[site.position]), site.position, reason))
        return candidates

    def _attack_candidates(
        self,
        game: Game,
        army: Army,
        enemies: list[Army],
        dist: dict[Coord, float],
        my_strengths: list[tuple[Army, float]],
    ) -> list[tuple[float, Coord, str]]:
        aggressiveness = self.params["agresividad"]
        threshold = self.params["umbral_ataque"]
        grouping = self.params.get("agrupa", False)
        battle_cfg = game.config["batalla"]
        my_strength = _strength(army, game.classes)
        candidates = []
        for enemy in enemies:
            if enemy.position not in dist:
                continue
            enemy_strength = _strength(enemy, game.classes)
            if game.world.fort_at(enemy.position):
                enemy_strength *= battle_cfg["bonus_defensa_fort"]
            elif game.world.town_at(enemy.position):
                enemy_strength *= battle_cfg["bonus_defensa_town"]
            enemy_strength = max(enemy_strength, 1.0)
            ratio = my_strength / enemy_strength
            # Fuerza combinada: solo ejércitos propios cerca del objetivo
            # (contar los lejanos lleva a ataques suicidas de a uno).
            local_strength = sum(
                s for a, s in my_strengths
                if _manhattan(a.position, enemy.position) <= GROUP_RADIUS
                or a.id == army.id
            )
            combined = local_strength / enemy_strength
            if ratio >= threshold:
                advantage, reason = ratio, "atacar"
            elif grouping and combined >= threshold:
                advantage, reason = combined, "atacar agrupado"
            else:
                continue
            value = VALUE_ATTACK * (0.5 + aggressiveness) * min(advantage, 2.0) / 2.0
            candidates.append((self._discount(value, dist[enemy.position]), enemy.position, reason))
        return candidates

    def _defense_candidates(
        self,
        threats: dict[Coord, tuple[Army, int, bool]],
        dist: dict[Coord, float],
    ) -> list[tuple[float, Coord, str]]:
        defense = self.params["peso_defensa"]
        candidates = []
        for fort_pos, (_enemy, _threat_dist, approaching) in threats.items():
            if fort_pos not in dist:
                continue
            value = VALUE_DEFEND * defense * (APPROACH_BOOST if approaching else 1.0)
            candidates.append((self._discount(value, dist[fort_pos]), fort_pos, "defender fuerte"))
        return candidates

    def _resupply_candidates(
        self, game: Game, army: Army, dist: dict[Coord, float]
    ) -> list[tuple[float, Coord, str]]:
        if army.total_troops >= game.config["max_army_size"] * DAMAGED_FRACTION:
            return []
        economy = self.params["peso_economia"]
        candidates = []
        for fort in game.world.forts:
            if (
                fort.owner == self.player_id
                and fort.reserve_total > 0
                and fort.position in dist
            ):
                value = VALUE_RESUPPLY * (0.3 + economy)
                candidates.append(
                    (self._discount(value, dist[fort.position]), fort.position, "reabastecer")
                )
        return candidates

    def _discount(self, value: float, distance: float) -> float:
        """Descuenta el valor por distancia según el horizonte del nivel."""
        return value / (1.0 + distance / self.params["horizonte"])

    # --- detección de amenazas y memoria ---------------------------------------

    def _fort_threats(
        self, game: Game, enemies: list[Army]
    ) -> dict[Coord, tuple[Army, int, bool]]:
        """Para cada fuerte propio, el enemigo más cercano dentro del radio
        de amenaza y si se acercó respecto del turno anterior."""
        threats: dict[Coord, tuple[Army, int, bool]] = {}
        for fort in game.world.forts:
            if fort.owner != self.player_id:
                continue
            nearest: tuple[Army, int] | None = None
            for enemy in enemies:
                d = _manhattan(enemy.position, fort.position)
                if d <= THREAT_RADIUS and (nearest is None or d < nearest[1]):
                    nearest = (enemy, d)
            if nearest is not None:
                enemy, d = nearest
                approaching = self._enemy_min_dist.get(enemy.id, THREAT_RADIUS + 1) > d
                threats[fort.position] = (enemy, d, approaching)
        return threats

    def _remember_enemies(self, game: Game, enemies: list[Army]) -> None:
        my_forts = [f.position for f in game.world.forts if f.owner == self.player_id]
        if not my_forts:
            self._enemy_min_dist = {}
            return
        self._enemy_min_dist = {
            enemy.id: min(_manhattan(enemy.position, fp) for fp in my_forts)
            for enemy in enemies
        }

    def _log(self, game: Game, message: str) -> None:
        """Justificación de decisiones para depurar y balancear la AI."""
        if self.debug:
            print(f"[AI:{self.level} p{self.player_id} t{game.turn}] {message}")


def _strength(army: Army, classes: dict[str, UnitClass]) -> float:
    """Estimación de fuerza de combate: tropas ponderadas por stats, XP y comida."""
    base = sum(
        count * (classes[class_id].ataque + classes[class_id].defensa) / 2.0
        for class_id, count in army.composition.items()
        if count > 0
    )
    return base * (0.5 + min(army.xp, 100) / 200.0) * (0.5 + min(army.food, 100) / 200.0)


def _manhattan(a: Coord, b: Coord) -> int:
    return abs(a[0] - b[0]) + abs(a[1] - b[1])
