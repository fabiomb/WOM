"""Observación del tablero: `Game` → descripción para el prompt del LLM.

`build_observation` arma un dict estructurado (testeable, determinista) con todo
lo que un jugador necesita para decidir: turno, comida, condición de victoria,
un mapa ASCII y las listas de sitios y ejércitos (propios con detalle completo,
enemigos con lo observable). `render_text` lo convierte en el texto compacto que
viaja en el mensaje al modelo.

No importa pygame ni la red: solo lee el `Game`.
"""

from __future__ import annotations

from wom.core.game import Game
from wom.core.worldmap import NEUTRAL, Terrain, WorldMap

# Símbolo de una sola letra por terreno para el mapa ASCII.
_TERRAIN_GLYPH = {
    Terrain.PLAINS: ".",
    Terrain.FOREST: "f",
    Terrain.MOUNTAIN: "^",
    Terrain.WATER: "~",
    Terrain.BRIDGE_H: "=",
    Terrain.BRIDGE_V: "=",
}


def _site_owner_tag(owner: int, player_id: int) -> str:
    if owner == NEUTRAL:
        return "neutral"
    return "propio" if owner == player_id else "enemigo"


def build_observation(game: Game, player_id: int) -> dict:
    """Proyecta el estado del juego a la vista de `player_id` (dict serializable).

    Los ejércitos propios se exponen con su composición exacta; los enemigos,
    con su posición y total de tropas (lo razonablemente observable). Las
    reservas de fuertes solo se muestran para los fuertes propios.
    """
    world = game.world
    me = game.players[player_id]
    enemy_ids = [p.id for p in game.players if p.id != player_id]

    my_armies = []
    enemy_armies = []
    for army in sorted(game.armies, key=lambda a: a.id):
        if army.owner == player_id:
            my_armies.append(
                {
                    "id": army.id,
                    "pos": list(army.position),
                    "composition": {c: n for c, n in army.composition.items() if n > 0},
                    "total": army.total_troops,
                    "xp": army.xp,
                    "food": army.food,
                }
            )
        else:
            enemy_armies.append(
                {
                    "id": army.id,
                    "owner": army.owner,
                    "pos": list(army.position),
                    "total": army.total_troops,
                }
            )

    forts = [
        {
            "pos": list(f.position),
            "owner": _site_owner_tag(f.owner, player_id),
            "reserve_total": f.reserve_total if f.owner == player_id else None,
        }
        for f in world.forts
    ]
    towns = [
        {"pos": list(t.position), "owner": _site_owner_tag(t.owner, player_id)}
        for t in world.towns
    ]

    return {
        "turn": game.turn,
        "turn_limit": game.turn_limit,
        "victory_mode": game.victory_mode.value,
        "you": {
            "player_id": player_id,
            "name": me.name,
            "food_stock": me.food,
            "troops_lost": me.troops_lost,
        },
        "enemies": enemy_ids,
        "map_size": [world.width, world.height],
        "ascii_map": _ascii_map(game, player_id),
        "forts": forts,
        "towns": towns,
        "my_armies": my_armies,
        "enemy_armies": enemy_armies,
    }


def _ascii_map(game: Game, player_id: int) -> list[str]:
    """Mapa ASCII con índices de columna/fila y marcadores de entidades.

    Marcadores (prioridad: ejército > fuerte > pueblo): ``A`` ejército propio,
    ``E`` enemigo; ``F``/``X``/``+`` fuerte propio/enemigo/neutral; ``T``/``Y``/``-``
    pueblo propio/enemigo/neutral. El terreno se ve donde no hay entidades.
    """
    world = game.world
    overlay: dict[tuple[int, int], str] = {}
    for t in world.towns:
        overlay[t.position] = {"propio": "T", "enemigo": "Y", "neutral": "-"}[
            _site_owner_tag(t.owner, player_id)
        ]
    for f in world.forts:
        overlay[f.position] = {"propio": "F", "enemigo": "X", "neutral": "+"}[
            _site_owner_tag(f.owner, player_id)
        ]
    for army in game.armies:
        overlay[army.position] = "A" if army.owner == player_id else "E"

    rows = [_column_header(world.width)]
    for y in range(world.height):
        cells = []
        for x in range(world.width):
            cells.append(overlay.get((x, y)) or _TERRAIN_GLYPH[world.terrain_at((x, y))])
        rows.append(f"{y:>2} " + "".join(cells))
    return rows


def _column_header(width: int) -> str:
    """Fila de encabezado con el dígito de las unidades de cada columna."""
    return "   " + "".join(str(x % 10) for x in range(width))


def render_text(game: Game, player_id: int) -> str:
    """Observación lista para el prompt (mapa ASCII + listas estructuradas)."""
    obs = build_observation(game, player_id)
    lines: list[str] = []
    lines.append(f"== Turno {obs['turn']} ==")
    if obs["turn_limit"] is not None:
        lines.append(f"Límite de turnos: {obs['turn_limit']}")
    lines.append(f"Modo de victoria: {obs['victory_mode']}")
    you = obs["you"]
    lines.append(
        f"Sos el jugador {you['player_id']} ({you['name']}). "
        f"Comida en stock: {you['food_stock']}. Tropas perdidas: {you['troops_lost']}."
    )
    w, h = obs["map_size"]
    lines.append(f"\nMapa {w}x{h} (x crece a la derecha, y hacia abajo):")
    lines.extend(obs["ascii_map"])
    lines.append(
        "Leyenda: . llano  f bosque  ^ montaña  ~ agua  = puente | "
        "A ejército propio  E enemigo | F/X/+ fuerte propio/enemigo/neutral | "
        "T/Y/- pueblo propio/enemigo/neutral"
    )

    lines.append("\nMis ejércitos:")
    if obs["my_armies"]:
        for a in obs["my_armies"]:
            comp = ", ".join(f"{n} {c}" for c, n in a["composition"].items()) or "vacío"
            lines.append(
                f"  #{a['id']} en {tuple(a['pos'])}: {comp} "
                f"(total {a['total']}, xp {a['xp']}, comida {a['food']})"
            )
    else:
        lines.append("  (ninguno)")

    lines.append("Ejércitos enemigos visibles:")
    if obs["enemy_armies"]:
        for a in obs["enemy_armies"]:
            lines.append(f"  #{a['id']} en {tuple(a['pos'])}: ~{a['total']} tropas")
    else:
        lines.append("  (ninguno)")

    lines.append("Fuertes:")
    for f in obs["forts"]:
        reserve = (
            f", reserva {f['reserve_total']}" if f["reserve_total"] is not None else ""
        )
        lines.append(f"  {tuple(f['pos'])}: {f['owner']}{reserve}")
    lines.append("Pueblos (producen comida):")
    for t in obs["towns"]:
        lines.append(f"  {tuple(t['pos'])}: {t['owner']}")

    return "\n".join(lines)


def passable_summary(world: WorldMap) -> str:
    """Una línea recordando qué terreno es intransitable (para el prompt)."""
    return "El agua (~) es intransitable; los puentes (=) la cruzan."
