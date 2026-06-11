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
- `wom/ui/` — the only package allowed to import pygame. Produces the same `Order` objects as the AI. Army merging is the exception to the orders flow: Shift+click on two adjacent own armies opens a confirm dialog and calls `Game.merge_armies` immediately (human-only convenience, not available to the AI). End-of-turn movement is animated in three phases (march → battle clash → retreat): the core records each army's traversed tiles in `Game.last_moves`, battle pairs in `Game.last_clashes` and who retreated in `Game.last_retreats` (transient, like `last_battles`, not serialized) and `wom/ui/animation.py` (pure Python, no pygame — unit-testable) interpolates them — during the clash both sides lunge at each other with a flash at the contact point, and retreat steps animate only after the clash. The battle result is *revealed* only when the clash ends (`TurnAnimation.result_revealed`): until then combatants show their pre-battle troop counts and the renderer hides this turn's crosses (`hide_new_crosses`); `GameScreen.end_turn` snapshots armies pre-turn so dying armies animate before vanishing (they disappear when their clash ends). The same module computes the spawn-highlight rings that mark the player's initial army during the first seconds of a new game (turn 0 only; dismissed on selection or first end-of-turn).
- `wom/persistence/` — JSON savegames in `saves/` (`savegame.py`: `save_game`/`load_game`/`list_saves`/`save_info`; all take an optional `directory`, which tests use with `tmp_path`). A savegame stores `format_version`, timestamp and `Game.to_dict()` — including the RNG state and each player's `ai_level` — so a loaded game continues deterministically. Balance config is re-read from `data/config/` on load, never stored. User preferences (music) live in `settings.py` → `settings.json` at the project root (gitignored), saved on every change.

Music (`wom/ui/music.py` + `music_overlay.py`): `MusicPlayer` builds a playlist from the settings folder (default `data/music`, mp3/ogg), starts on a random track, and advances via `MUSIC_END_EVENT`. The M key toggles a modal player overlay handled at the app-loop level (above any screen); the menu's Opciones mode configures it through the player's setters (each applies and persists immediately). Everything degrades to silent no-ops when the mixer or files are unavailable (headless tests fake `wom.ui.music.mixer`).

Key invariants:
- All randomness goes through the game's seeded `random.Random` (`Game.rng`) — battles and map generation must be deterministic given a seed (replays, tests).
- Game balance lives in `data/config/*.json` (unit classes, battle thresholds, AI weights), never hardcoded.
- Turn phases run in fixed order (see `core/game.py` docstring): orders → movement → battles → capture → production → recovery → victory check.
- Fort production accumulates in `Fort.reserve`; troops enter the map only via a voluntary `CreateArmyOrder` (or the initial spawn). Production order among a player's forts rotates by turn so captured forts also produce when food is scarce. An army parked on its own fort auto-resupplies from the reserve up to `max_army_size` (food refill costs player stock); an army parked on an own town refills food directly, without touching the stock. Capturing a fort destroys its reserve. Each player's cumulative losses are tracked in `Player.troops_lost` (serialized, shown in the HUD).
- Placeholder asset sizes are a contract with future final art: tiles 64×64, units 48×48, icons 32×32 (see `tools/gen_placeholders.py`). The script writes `_<name>.png` always (placeholder reference) and `<name>.png` only if missing — running it never overwrites installed final art. Water uses autotiling (`wom/ui/tiling.py`, pure): 15 shore variants covering all 16 coast combinations (`water_n/s/e/w`, corners `water_ne/nw/se/sw`, channels `water_ns/water_ew`, U-shapes `water_u_n/s/e/w` named by the open side, `water_single`), chosen per tile by which orthogonal neighbors are land. Bridges (`Terrain.BRIDGE_H/V`, passable) are built by mapgen after all water is painted and extend shore-to-shore — a bridge never ends in water; they count as water for shoreline purposes. For movement they are single-axis tunnels: `WorldMap.can_step` rejects lateral entry/exit (BRIDGE_H connects only east-west, BRIDGE_V only north-south) and `neighbors()` enforces it for pathfinding, retreats and the mapgen connectivity check.

## Roadmap status

All v1 milestones (M1–M6, defined in `docs/especificaciones.md` §8) are **done** and tested: core + headless (M1), playable pygame UI (M2), three validated AI levels (M3), menu + savegames (M4), gameplay/presentation polish — cover art menu, army merging, music, sound/video options (M5), and distributable builds (M6). `main.py` opens the menu (`wom/ui/menu_screen.py`); in-game saving via HUD button or G key, ESC asks for confirmation and returns to the menu. v1 explicitly excludes: tactical battle zoom, map editor, multiplayer.

Builds: `pyinstaller wom.spec` produces `dist/wom/` (onedir, windowed exe). All resource paths go through `wom/paths.py`: read-only `data/` resolves into the bundle (`sys._MEIPASS`) when frozen, writable `saves/` + `settings.json` sit next to the executable. `.github/workflows/build.yml` tests and builds Windows + Linux artifacts on `v*` tags or manually (PyInstaller can't cross-compile; the Linux build runs on ubuntu-latest).

Battle semantics: combat triggers when an army tries to *enter* an enemy's tile — armies never share a tile; each side fights from its own tile (own terrain bonus, defender gets fort/town bonus). Attacker classes with `ignora_bonus_fort` in `classes.json` (the arquero) cancel the defender's fort bonus for their own contribution — they fight the fort's garrison 1:1. Losers/retreaters fall back one tile, except an army standing on a fort: it never retreats (retreat only happens in open field), so a fort holder must be destroyed to dislodge it.

Site flags are owner-colored: `flag_red.png` (player 0), `flag_blue.png` (player 1), `flag.png` (gray, neutral); the mapping lives in `theme.flag_icon(owner)`.

The menu draws over `data/assets/title.png` (cover art with a central parchment scroll); the scroll's usable area is `SCROLL_AREA` in `menu_screen.py` as image fractions — keep them in sync if the art changes. Without the asset the menu falls back to the flat background.
