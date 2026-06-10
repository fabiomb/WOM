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
.venv\Scripts\python.exe tools\screenshot_menu.py    # render the 3 menu views to docs/screenshot_menu_*.png
.venv\Scripts\python.exe tools\simulate.py --games 30            # AI balance simulation (--detail for per-game)
```

Scripts in `tools/` need the project root on `PYTHONPATH` (`$env:PYTHONPATH='D:\dev\WOM'`); `main.py` and pytest do not. UI tests run headless via `SDL_VIDEODRIVER=dummy` (set inside the test files).

## Architecture

Strict three-layer separation — **`wom/core/` must never import pygame** (enforced by `tests/test_smoke.py::test_core_does_not_import_pygame`):

- `wom/core/` — pure game logic: map + random generator (`worldmap.py`, `mapgen.py`), armies (`army.py`), turn engine (`game.py`), auto-resolved battles (`battle.py`), victory conditions (`victory.py`), config loading (`config.py`). Runs headless for tests and mass AI-vs-AI simulation. The map generator paints coherent features over plains (mountain chains as directed walks, forest blobs grown from seeds, lakes, and a river with passable fords every few tiles) targeting `TERRAIN_TARGETS` proportions; connectivity is still flood-fill-verified with retries.
- `wom/ai/` — AI players. Same interface as a human player: `decide_orders(game) -> list[Order]`. The three difficulty levels share one codebase (an objective-scoring engine: capture/attack/defend/resupply, discounted by distance/horizon) and differ only in weights and capability flags from `data/config/ai.json` (`agrupa`, `coordina`, `evita_peligro` are dificil-only). Balance is validated with `tools/simulate.py` (mass AI-vs-AI, alternating sides); keep dificil > medio > facil when touching AI params.
- `wom/ui/` — the only package allowed to import pygame. Produces the same `Order` objects as the AI. Army merging is the exception to the orders flow: Shift+click on two adjacent own armies opens a confirm dialog and calls `Game.merge_armies` immediately (human-only convenience, not available to the AI). End-of-turn movement is animated: the core records each army's traversed tiles in `Game.last_moves` (transient, like `last_battles`, not serialized) and `wom/ui/animation.py` (pure Python, no pygame — unit-testable) interpolates them; `GameScreen.end_turn` snapshots armies pre-turn so dying armies animate before vanishing.
- `wom/persistence/` — JSON savegames in `saves/` (`savegame.py`: `save_game`/`load_game`/`list_saves`/`save_info`; all take an optional `directory`, which tests use with `tmp_path`). A savegame stores `format_version`, timestamp and `Game.to_dict()` — including the RNG state and each player's `ai_level` — so a loaded game continues deterministically. Balance config is re-read from `data/config/` on load, never stored.

Key invariants:
- All randomness goes through the game's seeded `random.Random` (`Game.rng`) — battles and map generation must be deterministic given a seed (replays, tests).
- Game balance lives in `data/config/*.json` (unit classes, battle thresholds, AI weights), never hardcoded.
- Turn phases run in fixed order (see `core/game.py` docstring): orders → movement → battles → capture → production → recovery → victory check.
- Fort production accumulates in `Fort.reserve`; troops enter the map only via a voluntary `CreateArmyOrder` (or the initial spawn). Production order among a player's forts rotates by turn so captured forts also produce when food is scarce. An army parked on its own fort auto-resupplies from the reserve up to `max_army_size` (food refill costs player stock); an army parked on an own town refills food directly, without touching the stock. Capturing a fort destroys its reserve. Each player's cumulative losses are tracked in `Player.troops_lost` (serialized, shown in the HUD).
- Placeholder asset sizes are a contract with future final art: tiles 64×64, units 48×48, icons 32×32 (see `tools/gen_placeholders.py`).

## Roadmap status

M1 (core + basic AI + headless), M2 (playable pygame UI: map render, mouse orders with auto-pathing and waypoints, end turn, game-over overlay), M3 (objective-scoring AI with three validated difficulty levels) and M4 (main menu + savegames) are **done** and tested. `main.py` opens the menu (`wom/ui/menu_screen.py`): new game (AI level, map size, victory mode), load game, quit; in-game saving via HUD button or G key, ESC returns to the menu. Remaining milestone: M5 (PyInstaller builds). Milestones defined in `docs/especificaciones.md` §8. v1 explicitly excludes: tactical battle zoom, map editor, multiplayer.

Battle semantics: combat triggers when an army tries to *enter* an enemy's tile — armies never share a tile; each side fights from its own tile (own terrain bonus, defender gets fort/town bonus). Attacker classes with `ignora_bonus_fort` in `classes.json` (the arquero) cancel the defender's fort bonus for their own contribution — they fight the fort's garrison 1:1. Losers/retreaters fall back one tile.

Site flags are owner-colored: `flag_red.png` (player 0), `flag_blue.png` (player 1), `flag.png` (gray, neutral); the mapping lives in `theme.flag_icon(owner)`.

The menu draws over `data/assets/title.png` (cover art with a central parchment scroll); the scroll's usable area is `SCROLL_AREA` in `menu_screen.py` as image fractions — keep them in sync if the art changes. Without the asset the menu falls back to the flat background.
