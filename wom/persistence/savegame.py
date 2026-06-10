"""Savegames en JSON legible (carpeta saves/).

Contenido: versión de formato, seed, parámetros de partida, mapa completo,
estado de ejércitos/forts/towns/jugadores, turno actual y modo de victoria.
Cargar un savegame reconstruye el `Game` exacto.
"""

from __future__ import annotations

from pathlib import Path

from wom.core.game import Game

SAVE_FORMAT_VERSION = 1
SAVES_DIR = Path(__file__).resolve().parents[2] / "saves"


def save_game(game: Game, name: str | None = None) -> Path:
    """Serializa la partida a saves/<nombre|timestamp>.json y devuelve la ruta."""
    raise NotImplementedError("M4: guardar partida")


def load_game(path: Path) -> Game:
    """Reconstruye una partida desde un savegame."""
    raise NotImplementedError("M4: cargar partida")


def list_saves() -> list[Path]:
    """Savegames disponibles, más reciente primero."""
    return sorted(SAVES_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
