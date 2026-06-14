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
- **Guarnecer**: un ejército sobre un fuerte propio "de frente" (enemigo a
  distancia <= GARRISON_RADIUS) valora quedarse (distancia 0, sin descuento),
  escalado por `peso_guarnicion`; un fuerte de frente sin defensor atrae al
  ejército propio más conveniente. Resuelve que la AI abandonara los fuertes
  recién producidos.

Con `coordina` activo (nivel difícil), los objetivos de captura,
reabastecimiento y guarnición ya asignados a un ejército no se ofrecen a los
demás en el mismo turno (salvo quedarse en el propio tile): los ejércitos se
reparten el mapa en vez de amontonarse sobre el mismo objetivo (los ataques
sí se comparten: eso es el fuego concentrado de `agrupa`).

Con `evita_peligro` activo (nivel difícil), el ruteo penaliza los tiles
cercanos a ejércitos enemigos más fuertes (DANGER_RADIUS/DANGER_COST):
en vez de chocar de frente en batallas parejas — que el azar decide — el
ejército las rodea y solo pelea cuando tiene ventaja. Es la maniobra que
le permite reaccionar a los movimientos del jugador.

`horizonte` regula cuánto pesa la distancia: el nivel fácil (horizonte 1)
solo ve lo inmediato; el difícil (horizonte 5) persigue objetivos
estratégicos lejanos.

Además, crea un ejército en cada fuerte cuya reserva alcanzó
`umbral_crear_ejercito` (más bajo = saca ejércitos más rápido).

**Reorganización** (emite las mismas órdenes que el humano, pero el humano
las aplica al instante): con `fusiona` activo, dos ejércitos propios aledaños
y pequeños de retaguardia se consolidan en uno más fuerte (`MergeArmyOrder`);
con `divide` activo, un ejército grande parado en un fuerte de frente deja una
guarnición y desprende un destacamento móvil (`SplitArmyOrder`). Los
ejércitos reorganizados esperan un turno y actúan ya consolidados/divididos.

**Personalidades**: al empezar la partida cada AI recibe una personalidad
(agresivo/estratega/moderado, configurable en `ai.json`) elegida de forma
determinista a partir de la seed. La personalidad NO cambia la pericia del
nivel (horizonte, agrupa, coordina, evita_peligro); modula el carácter
multiplicando los pesos (`agresividad`, `peso_economia`, `peso_defensa`,
`peso_guarnicion`, `umbral_ataque`) y puede habilitar/inhibir `fusiona` y
`divide`.

Cada decisión se loguea con su justificación si `debug=True` (requisito de
idea.md: AI "perfectamente documentada para mejorar y modificar").
"""

from __future__ import annotations

import random

from wom.core.army import Army
from wom.core.config import UnitClass, load_ai_config, load_ai_personalities
from wom.core.game import Game
from wom.core.orders import (
    CreateArmyOrder,
    MergeArmyOrder,
    MoveOrder,
    Order,
    SplitArmyOrder,
)
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
VALUE_GARRISON = 11.0  # valor de mantener una guarnición en un fuerte de frente
THREAT_RADIUS = 8      # distancia Manhattan a la que un enemigo "amenaza" un fuerte
GARRISON_RADIUS = 12   # radio Manhattan que vuelve "de frente" a un fuerte propio
APPROACH_BOOST = 1.5   # urgencia extra si la amenaza se está acercando
DAMAGED_FRACTION = 0.5  # debajo de esta fracción de tropas, considerar reabastecer
FOOD_LOW = 40          # debajo de esta comida, buscar dónde alimentarse
GROUP_RADIUS = 6       # radio Manhattan del fuego concentrado (niveles con `agrupa`)
DANGER_RADIUS = 2      # radio Manhattan de la zona de peligro de un enemigo fuerte
DANGER_COST = 10.0     # costo extra por tile peligroso (fuerza el rodeo)


def choose_personality(seed: int, player_id: int, names: list[str]) -> str | None:
    """Elige la personalidad de un jugador AI de forma determinista.

    Depende solo de la seed de la partida y del id del jugador: misma seed =>
    misma personalidad (reproducible al recargar una partida guardada).
    """
    if not names:
        return None
    rng = random.Random(seed * 977 + player_id * 131 + 7)
    return rng.choice(sorted(names))


class AIPlayer:
    """Decide las órdenes de un jugador AI en cada turno."""

    def __init__(
        self,
        player_id: int,
        level: str = "medio",
        debug: bool = False,
        personality: str | None = None,
    ):
        levels = load_ai_config()
        if level not in levels:
            raise ValueError(f"Nivel de AI desconocido: {level!r} (opciones: {list(levels)})")
        self.player_id = player_id
        self.level = level
        self.debug = debug
        # Pesos efectivos = pesos del nivel modulados por la personalidad.
        self._base_params = levels[level]
        self.personality = personality
        self.params = self._resolve_params(self._base_params, personality)
        # Memoria: distancia mínima de cada ejército enemigo a mis fuertes
        # en el turno anterior, para detectar amenazas que se acercan.
        self._enemy_min_dist: dict[int, int] = {}
        self._announced = False

    @staticmethod
    def _resolve_params(base: dict, personality: str | None) -> dict:
        """Combina los pesos del nivel con la personalidad.

        La personalidad aplica multiplicadores (``mult_*``) sobre los pesos
        numéricos y puede sobreescribir las capacidades booleanas
        (``fusiona``/``divide``). La dificultad sigue marcando la pericia
        (horizonte, agrupa, coordina, evita_peligro); la personalidad solo
        modula el carácter (cuánto ataca, defiende, guarnece, etc.).
        """
        params = dict(base)
        if not personality:
            return params
        profiles = load_ai_personalities()
        profile = profiles.get(personality)
        if profile is None:
            return params
        for key in (
            "agresividad",
            "peso_economia",
            "peso_defensa",
            "peso_guarnicion",
            "umbral_ataque",
        ):
            mult = profile.get(f"mult_{key}")
            if mult is not None:
                params[key] = params.get(key, 0.0) * mult
        params["agresividad"] = max(0.0, min(1.0, params.get("agresividad", 0.0)))
        for flag in ("fusiona", "divide"):
            if flag in profile:
                params[flag] = profile[flag]
        return params

    # --- API pública -------------------------------------------------------

    def decide_orders(self, game: Game) -> list[Order]:
        """Devuelve las órdenes del turno para todos los ejércitos propios."""
        if self.debug and not self._announced:
            self._log(
                game,
                f"personalidad '{self.personality or 'neutral'}' "
                f"(agresividad {self.params.get('agresividad'):.2f}, "
                f"guarnición {self.params.get('peso_guarnicion'):.2f}, "
                f"fusiona={self.params.get('fusiona')}, divide={self.params.get('divide')})",
            )
            self._announced = True
        orders: list[Order] = self._creation_orders(game)
        enemies = [a for a in game.armies if a.owner != self.player_id]
        threats = self._fort_threats(game, enemies)
        frontier = self._frontier_forts(game, enemies)
        my_strengths = [
            (a, _strength(a, game.classes)) for a in game.armies_of(self.player_id)
        ]
        # Reorganización (fusionar/dividir): los ejércitos involucrados quedan
        # "ocupados" este turno y no reciben orden de movimiento (actúan el
        # turno siguiente, ya consolidados o divididos).
        reorganized: set[int] = set()
        orders += self._merge_orders(game, enemies, reorganized)
        orders += self._split_orders(game, frontier, reorganized)
        claimed: set[Coord] = set()  # objetivos ya asignados (si coordina)
        for army in game.armies_of(self.player_id):
            if army.id in reorganized:
                continue
            order = self._army_order(
                game, army, enemies, threats, frontier, my_strengths, claimed
            )
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
        frontier: dict[Coord, bool],
        my_strengths: list[tuple[Army, float]],
        claimed: set[Coord],
    ) -> Order | None:
        danger = (
            self._danger_map(game, army, enemies, my_strengths)
            if self.params.get("evita_peligro", False)
            else None
        )
        dist, prev = dijkstra(
            game.world, army.position, game.config["costo_terreno"], extra_cost=danger
        )
        candidates = (
            self._capture_candidates(game, dist)
            + self._attack_candidates(game, army, enemies, dist, my_strengths)
            + self._defense_candidates(threats, dist)
            + self._resupply_candidates(game, army, dist)
            + self._garrison_candidates(game, army, frontier, dist)
        )
        if self.params.get("coordina", False):
            # Los objetivos exclusivos (capturar/reabastecer/guarnecer) no se
            # duplican; atacar y defender pueden compartirse entre ejércitos.
            # Quedarse en el propio tile (guarnecer in situ) nunca se filtra.
            candidates = [
                c for c in candidates
                if c[1] not in claimed
                or c[2].startswith(("atacar", "defender"))
                or c[1] == army.position
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
        damaged = army.total_troops < game.config["max_army_size"] * DAMAGED_FRACTION
        hungry = army.food < FOOD_LOW
        if not damaged and not hungry:
            return []
        economy = self.params["peso_economia"]
        value = VALUE_RESUPPLY * (0.3 + economy)
        candidates = []
        for fort in game.world.forts:
            if fort.owner != self.player_id or fort.position not in dist:
                continue
            # Recuperar tropas requiere reserva; recuperar comida, solo el fuerte.
            if (damaged and fort.reserve_total > 0) or hungry:
                candidates.append(
                    (self._discount(value, dist[fort.position]), fort.position, "reabastecer")
                )
        if hungry:
            for town in game.world.towns:
                if town.owner == self.player_id and town.position in dist:
                    candidates.append(
                        (self._discount(value, dist[town.position]), town.position, "alimentar")
                    )
        return candidates

    def _garrison_candidates(
        self,
        game: Game,
        army: Army,
        frontier: dict[Coord, bool],
        dist: dict[Coord, float],
    ) -> list[tuple[float, Coord, str]]:
        """Mantener (o acudir a) una guarnición en los fuertes de frente.

        Resuelve el problema de los ejércitos que abandonan sus fuertes recién
        producidos: un ejército sobre un fuerte propio cercano al enemigo
        valora quedarse (distancia 0, sin descuento); un fuerte de frente sin
        defensor atrae al ejército propio más conveniente.
        """
        weight = self.params.get("peso_guarnicion", 0.0)
        if weight <= 0 or not frontier:
            return []
        candidates = []
        for fort_pos, threatened in frontier.items():
            boost = APPROACH_BOOST if threatened else 1.0
            if army.position == fort_pos:
                # Ya guarnezco este fuerte: alto valor por no abandonarlo.
                candidates.append((VALUE_GARRISON * weight * boost, fort_pos, "guarnecer"))
            elif game.army_at(fort_pos) is None and fort_pos in dist:
                value = VALUE_GARRISON * weight * boost * 0.8
                candidates.append((self._discount(value, dist[fort_pos]), fort_pos, "guarnecer"))
        return candidates

    def _frontier_forts(self, game: Game, enemies: list[Army]) -> dict[Coord, bool]:
        """Fuertes propios con un enemigo dentro de GARRISON_RADIUS.

        El valor indica si la amenaza es inminente (enemigo dentro de
        THREAT_RADIUS), para subir la urgencia de la guarnición.
        """
        frontier: dict[Coord, bool] = {}
        for fort in game.world.forts:
            if fort.owner != self.player_id:
                continue
            nearest = min(
                (_manhattan(e.position, fort.position) for e in enemies),
                default=GARRISON_RADIUS + 1,
            )
            if nearest <= GARRISON_RADIUS:
                frontier[fort.position] = nearest <= THREAT_RADIUS
        return frontier

    # --- reorganización: fusionar y dividir ejércitos ------------------------

    def _merge_orders(
        self, game: Game, enemies: list[Army], reorganized: set[int]
    ) -> list[Order]:
        """Consolida ejércitos propios dispersos y débiles en la retaguardia.

        Dos ejércitos aledaños y pequeños (cada uno por debajo de
        `umbral_fusion` del máximo) se fusionan en uno solo más fuerte. No se
        toca a los que tienen un enemigo cerca (radio GROUP_RADIUS): esos se
        coordinan para el ataque (`agrupa`) en vez de gastar un turno
        fusionándose.
        """
        if not self.params.get("fusiona", False):
            return []
        max_size = game.config["max_army_size"]
        cap = max_size * self.params.get("umbral_fusion", 0.4)
        mine = sorted(game.armies_of(self.player_id), key=lambda a: a.id)

        def near_enemy(a: Army) -> bool:
            return any(_manhattan(a.position, e.position) <= GROUP_RADIUS for e in enemies)

        orders: list[Order] = []
        used: set[int] = set()
        for a in mine:
            if a.id in used or a.id in reorganized or a.total_troops >= cap or near_enemy(a):
                continue
            for b in mine:
                if b.id == a.id or b.id in used or b.id in reorganized:
                    continue
                if not _adjacent(a.position, b.position) or near_enemy(b):
                    continue
                if a.total_troops + b.total_troops > max_size:
                    continue
                src, tgt = (a, b) if a.total_troops <= b.total_troops else (b, a)
                orders.append(MergeArmyOrder(source_id=src.id, target_id=tgt.id))
                used.update({a.id, b.id})
                reorganized.update({a.id, b.id})
                self._log(game, f"fusionar ejército {src.id} en {tgt.id} en {tgt.position}")
                break
        return orders

    def _split_orders(
        self, game: Game, frontier: dict[Coord, bool], reorganized: set[int]
    ) -> list[Order]:
        """Divide un ejército grande parado en un fuerte de frente: deja una
        guarnición (el original) y desprende un destacamento móvil (el nuevo).

        Así el fuerte no queda desguarnecido mientras la AI proyecta fuerza:
        ataca la causa de "abandona los fuertes". El destacamento recibe
        órdenes el turno siguiente.
        """
        if not self.params.get("divide", False):
            return []
        threshold = self.params.get("umbral_division", 70)
        orders: list[Order] = []
        for army in sorted(game.armies_of(self.player_id), key=lambda a: a.id):
            if army.id in reorganized or army.total_troops < threshold:
                continue
            if army.position not in frontier:
                continue
            fort = game.world.fort_at(army.position)
            if fort is None or fort.owner != self.player_id:
                continue
            if not any(
                pos for pos in game.world.neighbors(army.position)
                if game.army_at(pos) is None
                and game.world.fort_at(pos) is None
                and game.world.town_at(pos) is None
            ):
                continue  # sin tile libre donde colocar el destacamento
            detachment = _split_composition(army)
            if detachment is None:
                continue
            orders.append(SplitArmyOrder(source_id=army.id, composition=detachment))
            reorganized.add(army.id)
            self._log(
                game,
                f"dividir ejército {army.id} en {army.position}: "
                f"guarnición + destacamento {dict(detachment)}",
            )
        return orders

    def _discount(self, value: float, distance: float) -> float:
        """Descuenta el valor por distancia según el horizonte del nivel."""
        return value / (1.0 + distance / self.params["horizonte"])

    def _danger_map(
        self,
        game: Game,
        army: Army,
        enemies: list[Army],
        my_strengths: list[tuple[Army, float]],
    ) -> dict[Coord, float]:
        """Costo extra alrededor de cada enemigo que no podemos vencer.

        Usa el mismo criterio que el ataque: si yo (o el grupo cercano, con
        `agrupa`) supero el umbral contra ese enemigo, no es peligro sino
        objetivo. El tile del enemigo no se penaliza con infinito: si el
        único camino pasa por ahí, la batalla puede valer la pena igual.
        """
        my_strength = max(_strength(army, game.classes), 1.0)
        threshold = self.params["umbral_ataque"]
        grouping = self.params.get("agrupa", False)
        danger: dict[Coord, float] = {}
        for enemy in enemies:
            enemy_strength = max(_strength(enemy, game.classes), 1.0)
            if my_strength / enemy_strength >= threshold:
                continue  # más débil que yo: no es peligro, es objetivo
            if grouping:
                local_strength = sum(
                    s for a, s in my_strengths
                    if _manhattan(a.position, enemy.position) <= GROUP_RADIUS
                    or a.id == army.id
                )
                if local_strength / enemy_strength >= threshold:
                    continue  # el grupo puede con él
            ex, ey = enemy.position
            for dx in range(-DANGER_RADIUS, DANGER_RADIUS + 1):
                for dy in range(-DANGER_RADIUS, DANGER_RADIUS + 1):
                    if abs(dx) + abs(dy) > DANGER_RADIUS:
                        continue
                    pos = (ex + dx, ey + dy)
                    if game.world.in_bounds(pos):
                        danger[pos] = danger.get(pos, 0.0) + DANGER_COST
        return danger

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


def _adjacent(a: Coord, b: Coord) -> bool:
    return _manhattan(a, b) == 1


def _split_composition(army: Army) -> tuple[tuple[str, int], ...] | None:
    """Reparte el ejército en guarnición (queda) y destacamento (sale).

    Devuelve la composición del destacamento (≈ mitad de las tropas) como
    tupla de pares ordenados, o None si no se puede dividir dejando al menos
    una tropa de cada lado.
    """
    total = army.total_troops
    if total < 2:
        return None
    detach_target = total // 2
    detachment: dict[str, int] = {}
    remaining = detach_target
    for class_id in sorted(army.composition):
        if remaining <= 0:
            break
        available = army.composition[class_id]
        take = min(available, remaining)
        if take > 0:
            detachment[class_id] = take
            remaining -= take
    moved = sum(detachment.values())
    if moved == 0 or moved >= total:
        return None
    return tuple(sorted(detachment.items()))
