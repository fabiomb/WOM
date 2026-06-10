"""Resolución automática de batallas (v1, sin zoom táctico).

La batalla ocurre cuando un ejército intenta entrar al tile de un enemigo:
cada bando pelea desde su propio tile (el atacante desde el tile adyacente),
por lo que cada uno usa el bonus de su propio terreno y el defensor suma el
bonus de fort/town si está en uno.

Fórmula de poder por ejército:

    poder = sum por clase [cantidad * stat * bonus_vs * bonus_terreno]
            * factor_comida(food)   # lineal entre factor_comida_min y 1.0
            * factor_xp(xp)         # lineal entre factor_xp_min y 1.0
            * rng.uniform(1 - sigma, 1 + sigma)

donde stat es `ataque` para el atacante y `defensa` para el defensor.

El ratio de poderes (atacante/defensor) decide el resultado:

    ratio >= umbral_victoria          -> ATTACKER_WINS
    ratio <= umbral_retirada          -> DEFENDER_WINS
    1 + margen_empate < ratio         -> DEFENDER_RETREATS
    ratio < 1 - margen_empate         -> ATTACKER_RETREATS
    si no                             -> DRAW

Pérdidas de tropas: fracción `perdida_base * ratio` para el defensor y
`perdida_base / ratio` para el atacante (cap `perdida_max`); el bando que
se retira suma `penalidad_retirada_tropas`. Toda batalla causa al menos
una baja por bando. La retirada suma además `penalidad_retirada_xp`.

Toda la aleatoriedad usa el RNG de la partida (seed) para que tests y
replays sean deterministas.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from enum import Enum, auto

from wom.core.army import Army
from wom.core.config import UnitClass
from wom.core.worldmap import WorldMap


class BattleOutcome(Enum):
    ATTACKER_WINS = auto()
    DEFENDER_WINS = auto()
    DRAW = auto()
    ATTACKER_RETREATS = auto()
    DEFENDER_RETREATS = auto()


# Bandos que deben retirarse según el resultado (el perdedor decisivo
# también abandona la posición si sobrevive).
ATTACKER_FALLS_BACK = {BattleOutcome.ATTACKER_RETREATS, BattleOutcome.DEFENDER_WINS}
DEFENDER_FALLS_BACK = {BattleOutcome.DEFENDER_RETREATS, BattleOutcome.ATTACKER_WINS}


@dataclass(frozen=True)
class BattleResult:
    outcome: BattleOutcome
    attacker_losses: dict[str, int]  # bajas por clase
    defender_losses: dict[str, int]
    attacker_xp_delta: int
    defender_xp_delta: int


def resolve_battle(
    attacker: Army,
    defender: Army,
    world: WorldMap,
    classes: dict[str, UnitClass],
    battle_config: dict,
    rng: random.Random,
) -> BattleResult:
    """Resuelve una batalla y devuelve el resultado (no muta los ejércitos)."""
    cfg = battle_config
    attacker_power = _army_power(
        attacker, defender, world, classes, cfg, rng, is_defender=False
    )
    defender_power = _army_power(
        defender, attacker, world, classes, cfg, rng, is_defender=True
    )
    ratio = attacker_power / max(defender_power, 1e-9)
    outcome = _classify(ratio, cfg)

    attacker_frac = min(cfg["perdida_base"] / ratio, cfg["perdida_max"])
    defender_frac = min(cfg["perdida_base"] * ratio, cfg["perdida_max"])
    if outcome in ATTACKER_FALLS_BACK:
        attacker_frac = min(attacker_frac + cfg["penalidad_retirada_tropas"], cfg["perdida_max"])
    if outcome in DEFENDER_FALLS_BACK:
        defender_frac = min(defender_frac + cfg["penalidad_retirada_tropas"], cfg["perdida_max"])

    attacker_xp, defender_xp = _xp_deltas(outcome, cfg)
    return BattleResult(
        outcome=outcome,
        attacker_losses=_losses(attacker, attacker_frac),
        defender_losses=_losses(defender, defender_frac),
        attacker_xp_delta=attacker_xp,
        defender_xp_delta=defender_xp,
    )


def _classify(ratio: float, cfg: dict) -> BattleOutcome:
    margin = cfg["margen_empate"]
    if ratio >= cfg["umbral_victoria"]:
        return BattleOutcome.ATTACKER_WINS
    if ratio <= cfg["umbral_retirada"]:
        return BattleOutcome.DEFENDER_WINS
    if ratio > 1 + margin:
        return BattleOutcome.DEFENDER_RETREATS
    if ratio < 1 - margin:
        return BattleOutcome.ATTACKER_RETREATS
    return BattleOutcome.DRAW


def _xp_deltas(outcome: BattleOutcome, cfg: dict) -> tuple[int, int]:
    winner = -cfg["xp_perdida_ganador"]
    loser = -cfg["xp_perdida_perdedor"]
    retreat = -cfg["penalidad_retirada_xp"]
    draw = -cfg["xp_perdida_empate"]
    match outcome:
        case BattleOutcome.ATTACKER_WINS:
            return winner, loser
        case BattleOutcome.DEFENDER_WINS:
            return loser, winner
        case BattleOutcome.DEFENDER_RETREATS:
            return winner, loser + retreat
        case BattleOutcome.ATTACKER_RETREATS:
            return loser + retreat, winner
        case _:
            return draw, draw


def _losses(army: Army, fraction: float) -> dict[str, int]:
    """Bajas por clase, proporcionales a la composición; mínimo 1 baja total."""
    losses = {
        class_id: int(count * fraction + 0.5)
        for class_id, count in army.composition.items()
        if count > 0
    }
    if sum(losses.values()) == 0 and army.total_troops > 0:
        biggest = max(army.composition, key=lambda c: army.composition[c])
        losses[biggest] = 1
    return losses


def _army_power(
    army: Army,
    enemy: Army,
    world: WorldMap,
    classes: dict[str, UnitClass],
    battle_config: dict,
    rng: random.Random,
    *,
    is_defender: bool,
) -> float:
    """Calcula el poder de combate de un ejército según la fórmula del módulo."""
    cfg = battle_config
    terrain = world.terrain_at(army.position).value
    enemy_total = max(enemy.total_troops, 1)

    base = 0.0
    for class_id, count in army.composition.items():
        if count <= 0:
            continue
        unit = classes[class_id]
        stat = unit.defensa if is_defender else unit.ataque
        bonus_vs = sum(
            (enemy_count / enemy_total) * unit.bonus_vs.get(enemy_class, 1.0)
            for enemy_class, enemy_count in enemy.composition.items()
            if enemy_count > 0
        ) or 1.0
        bonus_terrain = unit.bonus_terreno.get(terrain, 1.0)
        base += count * stat * bonus_vs * bonus_terrain

    food_min = cfg["factor_comida_min"]
    xp_min = cfg["factor_xp_min"]
    power = base
    power *= food_min + (1.0 - food_min) * min(army.food, 100) / 100
    power *= xp_min + (1.0 - xp_min) * min(army.xp, 100) / 100
    if is_defender:
        if world.fort_at(army.position):
            power *= cfg["bonus_defensa_fort"]
        elif world.town_at(army.position):
            power *= cfg["bonus_defensa_town"]
    sigma = cfg["sigma_aleatoriedad"]
    power *= rng.uniform(1 - sigma, 1 + sigma)
    return power
