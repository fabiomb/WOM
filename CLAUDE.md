# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

WOM is a turn-based 2D military strategy game (human vs AI in v1). The game design lives in `idea.md` and the full technical specification in `docs/especificaciones.md` — both in Spanish. The user communicates in Spanish; respond, document, and write docstrings in Spanish. Code identifiers are in English.

**Stack (decided)**: Python 3.13 + pygame-ce, JSON for config and savegames, pytest, PyInstaller for distribution (Windows + Linux; the Linux build must be done on Linux — WSL2 or CI).

## Commands

The virtualenv is `.venv\` (already created, pygame-ce and pytest installed).

```powershell
.venv\Scripts\python.exe -m pytest tests -v          # run all tests
.venv\Scripts\python.exe -m pytest tests/test_x.py::test_name  # single test
.venv\Scripts\python.exe main.py                     # run the game (pygame window)
.venv\Scripts\python.exe main.py --headless --seed 42            # AI vs AI console game
.venv\Scripts\python.exe main.py --headless --debug-ai           # log AI decisions
.venv\Scripts\python.exe tools\gen_placeholders.py   # regenerate placeholder PNGs
.venv\Scripts\python.exe tools\screenshot_m2.py      # render a game frame to docs/screenshot_m2.png (no window)
```

Scripts in `tools/` need the project root on `PYTHONPATH` (`$env:PYTHONPATH='D:\dev\WOM'`); `main.py` and pytest do not. UI tests run headless via `SDL_VIDEODRIVER=dummy` (set inside the test files).

## Architecture

Strict three-layer separation — **`wom/core/` must never import pygame** (enforced by `tests/test_smoke.py::test_core_does_not_import_pygame`):

- `wom/core/` — pure game logic: map + random generator (`worldmap.py`, `mapgen.py`), armies (`army.py`), turn engine (`game.py`), auto-resolved battles (`battle.py`), victory conditions (`victory.py`), config loading (`config.py`). Runs headless for tests and mass AI-vs-AI simulation.
- `wom/ai/` — AI players. Same interface as a human player: `decide_orders(game) -> list[Order]`. The three difficulty levels share one codebase and differ only in weights from `data/config/ai.json`.
- `wom/ui/` — the only package allowed to import pygame. Produces the same `Order` objects as the AI.
- `wom/persistence/` — JSON savegames in `saves/`.

Key invariants:
- All randomness goes through the game's seeded `random.Random` (`Game.rng`) — battles and map generation must be deterministic given a seed (replays, tests).
- Game balance lives in `data/config/*.json` (unit classes, battle thresholds, AI weights), never hardcoded.
- Turn phases run in fixed order (see `core/game.py` docstring): orders → movement → battles → capture → production → recovery → victory check.
- Fort production accumulates in `Fort.reserve`; troops enter the map only via a voluntary `CreateArmyOrder` (or the initial spawn). An army parked on its own fort auto-resupplies from the reserve up to `max_army_size`. Capturing a fort destroys its reserve.
- Placeholder asset sizes are a contract with future final art: tiles 64×64, units 48×48, icons 32×32 (see `tools/gen_placeholders.py`).

## Roadmap status

M1 (core + basic AI + headless) and M2 (playable pygame UI: map render, mouse orders with auto-pathing and waypoints, end turn, game-over overlay) are **done** and tested. `main.py` starts a human-vs-AI game directly (no menu yet). Remaining milestones: M3 (medium/hard AI in `wom/ai/ai_player.py` — easy strategy currently shared by all levels; balance tuning), M4 (menu + savegames in `wom/persistence/savegame.py`), M5 (PyInstaller builds). Milestones defined in `docs/especificaciones.md` §8. v1 explicitly excludes: tactical battle zoom, map editor, multiplayer.

Battle semantics: combat triggers when an army tries to *enter* an enemy's tile — armies never share a tile; each side fights from its own tile (own terrain bonus, defender gets fort/town bonus). Losers/retreaters fall back one tile.
