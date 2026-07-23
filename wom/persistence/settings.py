"""Preferencias del usuario (settings.json en la raíz del proyecto).

A diferencia de data/config/ (balance del juego, solo lectura), acá viven
las preferencias del usuario — hoy, la música. Se guardan en cada cambio y
se releen al iniciar el juego; si el archivo falta o está roto se usan los
defaults.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
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
    # Efectos de sonido (SFX): sonido ambiente de la batalla y del movimiento
    # de tropas. Independientes de la música (usan canales del mixer, no el
    # streaming de `mixer.music`), con su propio interruptor y volumen.
    sfx_enabled: bool = True
    sfx_volume: float = 0.7  # 0.0 a 1.0
    video_resolution: str = "1920x1080"  # tamaño físico de la ventana
    video_maximized: bool = False  # arrancar maximizado
    # Multijugador por Internet: nombre del jugador y servidores guardados
    # (cada uno: {"name", "host", "port"}). Los administra el navegador de
    # servidores (S6) y se persisten como el resto de las preferencias.
    player_name: str = "Jugador"
    servers: list = field(default_factory=list)
    # Rival LLM (Multijugador → Jugar contra AI LLM): backend configurado desde
    # el juego. `llm_effort` vacío = sin razonamiento extendido (solo aplica a
    # Anthropic). La API key se guarda acá (settings.json está gitignoreado).
    llm_provider: str = "ollama"
    llm_model: str = "gemma3"
    llm_name: str = "LLM"
    llm_effort: str = ""
    llm_api_key: str = ""


def add_server(servers: list, name: str, host: str, port: int) -> list:
    """Devuelve una lista nueva con el servidor agregado al final."""
    return [*servers, {"name": name, "host": host, "port": int(port)}]


def update_server(servers: list, index: int, name: str, host: str, port: int) -> list:
    """Devuelve una lista nueva con el servidor `index` reemplazado."""
    out = [dict(s) for s in servers]
    if 0 <= index < len(out):
        out[index] = {"name": name, "host": host, "port": int(port)}
    return out


def remove_server(servers: list, index: int) -> list:
    """Devuelve una lista nueva sin el servidor `index`."""
    return [dict(s) for i, s in enumerate(servers) if i != index]


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
