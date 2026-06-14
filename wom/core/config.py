"""Carga y validación de los archivos de configuración (data/config/)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from wom.paths import resource_root

CONFIG_DIR = resource_root() / "data" / "config"


@dataclass(frozen=True)
class UnitClass:
    """Definición de una clase de unidad (cargada de classes.json)."""

    id: str
    nombre: str
    velocidad: int
    ataque: int
    defensa: int
    bonus_terreno: dict[str, float]
    bonus_vs: dict[str, float]
    # Al atacar un fuerte, la clase no sufre el bonus de defensa del fuerte
    # (pelea 1:1 contra el defensor); p. ej. los arqueros disparan por
    # encima de las murallas.
    ignora_bonus_fort: bool = False


def load_unit_classes(path: Path | None = None) -> dict[str, UnitClass]:
    """Carga las clases de unidad desde classes.json."""
    raw = json.loads((path or CONFIG_DIR / "classes.json").read_text(encoding="utf-8"))
    return {
        class_id: UnitClass(
            id=class_id,
            nombre=data["nombre"],
            velocidad=data["velocidad"],
            ataque=data["ataque"],
            defensa=data["defensa"],
            bonus_terreno=data.get("bonus_terreno", {}),
            bonus_vs=data.get("bonus_vs", {}),
            ignora_bonus_fort=data.get("ignora_bonus_fort", False),
        )
        for class_id, data in raw.items()
    }


def load_game_config(path: Path | None = None) -> dict:
    """Carga los parámetros generales del juego desde game.json."""
    return json.loads((path or CONFIG_DIR / "game.json").read_text(encoding="utf-8"))


def _load_ai_raw(path: Path | None = None) -> dict:
    return json.loads((path or CONFIG_DIR / "ai.json").read_text(encoding="utf-8"))


def load_ai_config(path: Path | None = None) -> dict:
    """Carga los parámetros de los niveles de AI desde ai.json.

    Acepta el formato nuevo (``{"niveles": {...}, "personalidades": {...}}``)
    y el antiguo (los niveles en la raíz), devolviendo siempre el diccionario
    de niveles.
    """
    raw = _load_ai_raw(path)
    return raw.get("niveles", raw)


def load_ai_personalities(path: Path | None = None) -> dict:
    """Carga las personalidades de AI desde ai.json (vacío si no hay)."""
    return _load_ai_raw(path).get("personalidades", {})
