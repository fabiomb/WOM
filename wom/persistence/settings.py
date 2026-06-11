"""Preferencias del usuario (settings.json en la raíz del proyecto).

A diferencia de data/config/ (balance del juego, solo lectura), acá viven
las preferencias del usuario — hoy, la música. Se guardan en cada cambio y
se releen al iniciar el juego; si el archivo falta o está roto se usan los
defaults.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

from wom.paths import user_root

SETTINGS_PATH = user_root() / "settings.json"
DEFAULT_MUSIC_FOLDER = "data/music"


@dataclass
class Settings:
    music_enabled: bool = True
    music_volume: float = 0.7  # 0.0 a 1.0
    music_folder: str = DEFAULT_MUSIC_FOLDER  # relativa a la raíz o absoluta
    music_shuffle: bool = True  # False = secuencial
    video_resolution: str = "1280x720"  # tamaño físico de la ventana
    video_maximized: bool = False  # arrancar maximizado


def load_settings(path: Path | None = None) -> Settings:
    """Lee settings.json; ante cualquier problema devuelve los defaults."""
    path = path or SETTINGS_PATH
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return Settings()
    known = Settings.__dataclass_fields__
    return Settings(**{k: v for k, v in data.items() if k in known})


def save_settings(settings: Settings, path: Path | None = None) -> Path:
    path = path or SETTINGS_PATH
    path.write_text(
        json.dumps(asdict(settings), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return path
