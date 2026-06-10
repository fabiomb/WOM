"""Savegames en JSON legible (carpeta saves/).

Contenido: versión de formato, fecha de guardado y el estado completo del
`Game` (seed, mapa, ejércitos, jugadores, turno, modo de victoria y estado
del RNG). Cargar un savegame reconstruye la partida exacta; la config de
balance se relee de data/config/ (no viaja en el archivo).
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from wom.core.game import Game

SAVE_FORMAT_VERSION = 1
SAVES_DIR = Path(__file__).resolve().parents[2] / "saves"


def save_game(game: Game, name: str | None = None, directory: Path | None = None) -> Path:
    """Serializa la partida a saves/<nombre|timestamp>.json y devuelve la ruta."""
    directory = directory or SAVES_DIR
    directory.mkdir(parents=True, exist_ok=True)
    if name is None:
        name = datetime.now().strftime("partida_%Y%m%d_%H%M%S")
    payload = {
        "format_version": SAVE_FORMAT_VERSION,
        "saved_at": datetime.now().isoformat(timespec="seconds"),
        "game": game.to_dict(),
    }
    path = directory / f"{name}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def load_game(path: Path) -> Game:
    """Reconstruye una partida desde un savegame."""
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    version = payload.get("format_version")
    if version != SAVE_FORMAT_VERSION:
        raise ValueError(
            f"savegame con formato v{version}; esta versión del juego usa v{SAVE_FORMAT_VERSION}"
        )
    return Game.from_dict(payload["game"])


def list_saves(directory: Path | None = None) -> list[Path]:
    """Savegames disponibles, más reciente primero."""
    directory = directory or SAVES_DIR
    return sorted(directory.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)


def save_info(path: Path) -> dict:
    """Resumen liviano de un savegame para mostrarlo en el menú de carga."""
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    game = payload["game"]
    return {
        "name": path.stem,
        "saved_at": payload.get("saved_at", ""),
        "turn": game["turn"],
        "players": [p["name"] for p in game["players"]],
        "victory_mode": game["victory_mode"],
    }
