"""Punto de entrada de WOM.

Uso:
    python main.py                        # abre el juego (UI pygame)
    python main.py --headless             # partida AI vs AI por consola
    python main.py --headless --seed 42   # partida reproducible
    python main.py --headless --debug-ai  # loguea las decisiones de la AI
"""

from __future__ import annotations

import argparse


def main() -> None:
    parser = argparse.ArgumentParser(description="WOM - Juego de estrategia militar")
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Simula una partida AI vs AI por consola, sin abrir ventana",
    )
    parser.add_argument("--seed", type=int, default=None, help="Seed de la partida")
    parser.add_argument(
        "--level",
        nargs=2,
        default=["facil", "facil"],
        metavar=("AI0", "AI1"),
        help="Niveles de las dos AI (facil/medio/dificil)",
    )
    parser.add_argument(
        "--debug-ai",
        action="store_true",
        help="Imprime la justificación de cada decisión de la AI",
    )
    args = parser.parse_args()

    if args.headless:
        from wom.headless import run_headless

        run_headless(seed=args.seed, levels=tuple(args.level), debug_ai=args.debug_ai)
        return

    from wom.ui.app import run

    # En modo UI el rival es la AI 1: su nivel es el segundo valor de --level.
    run(seed=args.seed, ai_level=args.level[1])


if __name__ == "__main__":
    main()
