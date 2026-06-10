"""Simulación masiva AI vs AI para balancear niveles y clases (M3).

Corre N partidas headless por cada enfrentamiento de niveles, alternando
los lados (la mitad de las partidas cada nivel juega como P0) para anular
el sesgo de posición inicial, y reporta victorias, empates y duración.

Uso:
    python tools/simulate.py                       # los 3 cruces, 20 partidas c/u
    python tools/simulate.py --games 50
    python tools/simulate.py --pairs dificil:facil --games 30 --seed 7000
"""

from __future__ import annotations

import argparse
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from wom.headless import run_headless  # noqa: E402

DEFAULT_PAIRS = ["facil:medio", "medio:dificil", "facil:dificil"]


def simulate_pair(
    level_a: str, level_b: str, games: int, base_seed: int, detail: bool = False
) -> None:
    wins: Counter[str] = Counter()
    turns_total = 0
    start = time.time()
    for i in range(games):
        # Alternar lados para anular la ventaja de posición inicial.
        levels = (level_a, level_b) if i % 2 == 0 else (level_b, level_a)
        result, game = run_headless(
            seed=base_seed + i, levels=levels, quiet=True, return_game=True
        )
        winner = levels[result.winner] if result.winner is not None else "empate"
        wins[winner] += 1
        turns_total += game.turn
        if detail:
            stats = []
            for player in game.players:
                troops = sum(a.total_troops for a in game.armies_of(player.id))
                territory = sum(
                    1 for s in (*game.world.forts, *game.world.towns)
                    if s.owner == player.id
                )
                stats.append(
                    f"{levels[player.id]}: {troops}trp {territory}ter"
                )
            mode = result.mode.value if result.mode else "?"
            print(
                f"  seed {base_seed + i}: gana {winner:<8} por {mode:<6} "
                f"t{game.turn:<3} [{' | '.join(stats)}]"
            )
    elapsed = time.time() - start
    print(f"\n{level_a} vs {level_b}  ({games} partidas, {elapsed:.1f}s)")
    for level in (level_a, level_b, "empate"):
        if wins[level]:
            print(f"  {level:>8}: {wins[level]:>3}  ({100 * wins[level] / games:.0f}%)")
    print(f"  duración promedio: {turns_total / games:.1f} turnos")


def main() -> None:
    parser = argparse.ArgumentParser(description="Balance AI vs AI")
    parser.add_argument("--games", type=int, default=20, help="Partidas por cruce")
    parser.add_argument("--seed", type=int, default=5000, help="Seed base")
    parser.add_argument(
        "--pairs", nargs="*", default=DEFAULT_PAIRS,
        help="Cruces nivel:nivel (default: los tres)",
    )
    parser.add_argument(
        "--detail", action="store_true", help="Imprime el resultado de cada partida"
    )
    args = parser.parse_args()
    for pair in args.pairs:
        level_a, level_b = pair.split(":")
        simulate_pair(level_a, level_b, args.games, args.seed, detail=args.detail)


if __name__ == "__main__":
    main()
