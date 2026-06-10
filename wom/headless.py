"""Simulación headless AI vs AI por consola (sin pygame).

Sirve para validar el core (M1) y, en M3, para correr partidas masivas
de balanceo. Determinista: misma seed => misma partida.
"""

from __future__ import annotations

from wom.ai.ai_player import AIPlayer
from wom.core.game import Game, Player
from wom.core.mapgen import MapParams
from wom.core.victory import VictoryMode, VictoryResult


def run_headless(
    seed: int | None = None,
    map_params: MapParams | None = None,
    levels: tuple[str, str] = ("facil", "facil"),
    victory_mode: VictoryMode = VictoryMode.TIME,
    debug_ai: bool = False,
    quiet: bool = False,
    return_game: bool = False,
) -> VictoryResult | tuple[VictoryResult, Game]:
    """Corre una partida AI vs AI completa y devuelve el resultado.

    Con `return_game=True` devuelve también el `Game` final (lo usa la
    simulación masiva de balance para extraer estadísticas).
    """
    params = map_params or MapParams(seed=seed)
    if seed is not None and params.seed != seed:
        params = MapParams(params.width, params.height, params.n_forts, params.n_towns, seed)
    players = [
        Player(0, f"AI Rojo ({levels[0]})", is_ai=True, ai_level=levels[0]),
        Player(1, f"AI Azul ({levels[1]})", is_ai=True, ai_level=levels[1]),
    ]
    game = Game.new(params, players, victory_mode)
    ais = [AIPlayer(p.id, levels[p.id], debug=debug_ai) for p in players]

    def say(msg: str) -> None:
        if not quiet:
            print(msg)

    say(f"Partida seed={game.seed}, mapa {params.width}x{params.height}, "
        f"modo {victory_mode.value}, límite {game.config['turnos_limite_default']} turnos")

    limit = game.config["turnos_limite_default"]
    result = VictoryResult(is_over=False, winner=None)
    for _ in range(limit + 1):
        orders = [order for ai in ais for order in ai.decide_orders(game)]
        result = game.run_turn(orders)
        say(f"  turno {game.turn:>3}: " + " | ".join(_player_stats(game, p.id) for p in players))
        if result.is_over:
            break

    if result.winner is not None:
        say(f"GANADOR: {players[result.winner].name} por {result.mode.value} ({result.reason})")
    else:
        say(f"EMPATE ({result.reason})")
    return (result, game) if return_game else result


def _player_stats(game: Game, player_id: int) -> str:
    armies = game.armies_of(player_id)
    forts = sum(1 for f in game.world.forts if f.owner == player_id)
    towns = sum(1 for t in game.world.towns if t.owner == player_id)
    troops = sum(a.total_troops for a in armies)
    reserve = sum(f.reserve_total for f in game.world.forts if f.owner == player_id)
    return (f"P{player_id}: {len(armies)} ejércitos, {troops} tropas, "
            f"reserva {reserve}, {forts}F {towns}T, comida {game.players[player_id].food}")
