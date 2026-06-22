"""Gramática de acciones de alto nivel del LLM ↔ `Order` del core.

El modelo NO emite paths tile-por-tile ni ids inventados: emite **intenciones**
en JSON (mover el ejército N hacia un tile, crear en un fuerte, fusionar, dividir,
transferir) y este módulo las traduce a `Order` válidas, calculando los caminos
con el pathfinding del core y descartando lo inválido con un motivo (que se le
puede devolver al modelo como feedback).

Formato de cada acción (lista JSON):

    {"action": "move",     "army": 7, "to": [12, 4]}
    {"action": "create",   "fort": [3, 5]}
    {"action": "merge",    "source": 7, "target": 8}
    {"action": "split",    "source": 7, "detach": {"soldado": 10}}
    {"action": "transfer", "source": 7, "target": 8, "troops": {"arquero": 5}}
    {"action": "wait"}     # pasar (sin efecto)

La traducción es defensiva: el lockstep ya filtra por dueño, pero acá validamos
además existencia, adyacencia, alcanzabilidad y cantidades, para no gastar
órdenes y para poder explicarle al modelo qué rechazó.
"""

from __future__ import annotations

from wom.core.game import Game
from wom.core.orders import (
    CreateArmyOrder,
    MergeArmyOrder,
    MoveOrder,
    Order,
    SplitArmyOrder,
    TransferTroopsOrder,
)
from wom.core.pathfind import army_terrain_costs, shortest_path
from wom.core.worldmap import Coord

# Sinónimos tolerados para las claves (los modelos pequeños no siempre respetan
# el nombre exacto). Cada acción mapea a su normalizador.
_MOVE_TO_KEYS = ("to", "dest", "destination", "tile", "target_tile")
_TROOPS_KEYS = ("troops", "composition", "detach", "units")


def translate_actions(
    actions: list[dict], game: Game, player_id: int
) -> tuple[list[Order], list[str]]:
    """Convierte la lista de acciones del modelo en órdenes válidas.

    Devuelve `(orders, warnings)`: las órdenes traducidas y una lista de motivos
    por cada acción descartada (para log o feedback al modelo).
    """
    orders: list[Order] = []
    warnings: list[str] = []
    for raw in actions:
        if not isinstance(raw, dict):
            warnings.append(f"acción no es un objeto: {raw!r}")
            continue
        kind = str(raw.get("action", "")).lower().strip()
        try:
            order = _translate_one(kind, raw, game, player_id)
        except _Invalid as exc:
            warnings.append(str(exc))
            continue
        if order is not None:
            orders.append(order)
    return orders, warnings


class _Invalid(Exception):
    """Acción rechazada con un motivo legible."""


def _translate_one(kind: str, raw: dict, game: Game, player_id: int) -> Order | None:
    if kind in ("wait", "pass", "end", "none", ""):
        return None
    if kind == "move":
        return _move(raw, game, player_id)
    if kind == "create":
        return _create(raw, game, player_id)
    if kind == "merge":
        return _merge(raw, game, player_id)
    if kind == "split":
        return _split(raw, game, player_id)
    if kind == "transfer":
        return _transfer(raw, game, player_id)
    raise _Invalid(f"acción desconocida: {kind!r}")


# --- traductores por tipo -------------------------------------------------


def _move(raw: dict, game: Game, player_id: int) -> MoveOrder:
    army = _own_army(raw.get("army"), game, player_id)
    dest = _coord(_first(raw, _MOVE_TO_KEYS), "destino")
    if not game.world.in_bounds(dest):
        raise _Invalid(f"move #{army.id}: destino {dest} fuera del mapa")
    if not game.world.is_passable(dest):
        raise _Invalid(f"move #{army.id}: destino {dest} es intransitable (agua)")
    if dest == army.position:
        raise _Invalid(f"move #{army.id}: ya está en {dest}")
    terrain_costs = army_terrain_costs(
        game.config["costo_terreno"], army.composition,
        game.config.get("costo_terreno_clase"),
    )
    path = shortest_path(game.world, army.position, dest, terrain_costs)
    if not path:
        raise _Invalid(f"move #{army.id}: no hay camino hasta {dest}")
    return MoveOrder(army_id=army.id, path=tuple(path))


def _create(raw: dict, game: Game, player_id: int) -> CreateArmyOrder:
    pos = _coord(raw.get("fort", raw.get("pos")), "fuerte")
    fort = game.world.fort_at(pos)
    if fort is None or fort.owner != player_id:
        raise _Invalid(f"create: {pos} no es un fuerte propio")
    if fort.reserve_total <= 0:
        raise _Invalid(f"create: el fuerte {pos} no tiene reserva")
    if game.army_at(pos) is not None:
        raise _Invalid(f"create: ya hay un ejército en el fuerte {pos}")
    return CreateArmyOrder(position=pos)


def _merge(raw: dict, game: Game, player_id: int) -> MergeArmyOrder:
    source = _own_army(raw.get("source"), game, player_id)
    target = _own_army(raw.get("target"), game, player_id)
    if source.id == target.id:
        raise _Invalid("merge: origen y destino son el mismo ejército")
    if not _adjacent(source.position, target.position):
        raise _Invalid(f"merge: #{source.id} y #{target.id} no son adyacentes")
    return MergeArmyOrder(source_id=source.id, target_id=target.id)


def _split(raw: dict, game: Game, player_id: int) -> SplitArmyOrder:
    source = _own_army(raw.get("source"), game, player_id)
    detach = _troops(_first(raw, _TROOPS_KEYS), game)
    detached = sum(detach.values())
    if detached <= 0:
        raise _Invalid(f"split #{source.id}: no especifica tropas a separar")
    for cls, n in detach.items():
        if n > source.composition.get(cls, 0):
            raise _Invalid(
                f"split #{source.id}: pide {n} {cls} pero solo hay "
                f"{source.composition.get(cls, 0)}"
            )
    if detached >= source.total_troops:
        raise _Invalid(f"split #{source.id}: debe quedar al menos 1 tropa en el origen")
    if not _free_adjacent(source.position, game):
        raise _Invalid(f"split #{source.id}: no hay tile libre adyacente para el nuevo ejército")
    return SplitArmyOrder(
        source_id=source.id,
        composition=tuple((cls, n) for cls, n in detach.items() if n > 0),
    )


def _transfer(raw: dict, game: Game, player_id: int) -> TransferTroopsOrder:
    source = _own_army(raw.get("source"), game, player_id)
    target = _own_army(raw.get("target"), game, player_id)
    if source.id == target.id:
        raise _Invalid("transfer: origen y destino son el mismo ejército")
    if not _adjacent(source.position, target.position):
        raise _Invalid(f"transfer: #{source.id} y #{target.id} no son adyacentes")
    troops = _troops(_first(raw, _TROOPS_KEYS), game)
    if sum(troops.values()) <= 0:
        raise _Invalid(f"transfer #{source.id}→#{target.id}: no especifica tropas")
    for cls, n in troops.items():
        if n > source.composition.get(cls, 0):
            raise _Invalid(
                f"transfer #{source.id}: pide {n} {cls} pero solo hay "
                f"{source.composition.get(cls, 0)}"
            )
    return TransferTroopsOrder(
        source_id=source.id,
        target_id=target.id,
        composition=tuple((cls, n) for cls, n in troops.items() if n > 0),
    )


# --- helpers de validación ------------------------------------------------


def _own_army(army_id, game: Game, player_id: int):
    army_id = _coerce_id(army_id)
    if army_id is None:
        raise _Invalid("id de ejército inválido")
    army = game.army_by_id(army_id)
    if army is None:
        raise _Invalid(f"el ejército #{army_id} no existe")
    if army.owner != player_id:
        raise _Invalid(f"el ejército #{army_id} no es tuyo")
    return army


def _coerce_id(value) -> int | None:
    """Acepta un id como int, o como str tipo ``"#7"``/``"7"`` (los modelos lo
    copian del prompt, que muestra los ejércitos como ``#id``)."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value.strip().lstrip("#").strip())
        except ValueError:
            return None
    return None


def _coord(value, what: str) -> Coord:
    if isinstance(value, dict) and "x" in value and "y" in value:
        value = [value["x"], value["y"]]
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise _Invalid(f"{what} inválido: {value!r} (se espera [x, y])")
    try:
        return (int(value[0]), int(value[1]))
    except (TypeError, ValueError):
        raise _Invalid(f"{what} inválido: {value!r}")


def _troops(value, game: Game) -> dict[str, int]:
    if not isinstance(value, dict):
        raise _Invalid(f"tropas inválidas: {value!r} (se espera {{clase: cantidad}})")
    result: dict[str, int] = {}
    for cls, n in value.items():
        if cls not in game.classes:
            raise _Invalid(f"clase de unidad desconocida: {cls!r}")
        try:
            count = int(n)
        except (TypeError, ValueError):
            raise _Invalid(f"cantidad inválida para {cls}: {n!r}")
        if count > 0:
            result[cls] = count
    return result


def _adjacent(a: Coord, b: Coord) -> bool:
    return abs(a[0] - b[0]) + abs(a[1] - b[1]) == 1


def _free_adjacent(pos: Coord, game: Game) -> bool:
    for n in game.world.neighbors(pos):
        if (
            game.army_at(n) is None
            and game.world.fort_at(n) is None
            and game.world.town_at(n) is None
        ):
            return True
    return False


def _first(raw: dict, keys: tuple[str, ...]):
    for key in keys:
        if key in raw:
            return raw[key]
    return None
