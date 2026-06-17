"""Mapas y escenarios compartibles en un contenedor `.wom`.

Un `.wom` es un ZIP que empaqueta `scenario.json` (el documento) y, opcional,
`image.png` (la ilustración del escenario). El JSON guarda el *setup inicial*
de una partida —terreno, sitios, tropas sembradas y jugadores— más metadata
(título, descripción, modo de victoria, nivel de IA del rival). NO es un
savegame: no lleva estado vivo (RNG, turno); al cargarlo se arranca una
partida nueva en turno 0 (`Game.from_setup`).

Mismo formato para las dos cosas que pide el juego:
- **Escenario**: `.wom` con título (se muestra su intro y se juega con la IA y
  la victoria que trae adentro).
- **Mapa**: `.wom` sin título (terreno + tropas para jugar como partida nueva,
  eligiendo IA/victoria en el menú).

Este módulo es stdlib pura (zipfile + json): NO importa pygame. La imagen viaja
como bytes crudos; la UI los convierte a Surface.
"""

from __future__ import annotations

import io
import json
import re
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from wom.core.game import Game, Player
from wom.core.victory import VictoryMode
from wom.core.worldmap import WorldMap
from wom.paths import resource_root, user_root

SCENARIO_FORMAT_VERSION = 1
SCENARIO_EXT = ".wom"
JSON_NAME = "scenario.json"
IMAGE_NAME = "image.png"

# Escenarios que se distribuyen con el juego (solo lectura) y carpeta donde el
# editor guarda y el usuario comparte sus mapas (escribible, gitignored).
BUNDLED_SCENARIOS_DIR = resource_root() / "data" / "scenarios"
MAPS_DIR = user_root() / "maps"


@dataclass
class ScenarioDoc:
    """Documento de un `.wom`: el setup de la partida más su metadata."""

    world: WorldMap
    players: list[Player]
    army_specs: list[dict] = field(default_factory=list)
    title: str = ""
    description: str = ""
    victory_mode: VictoryMode = VictoryMode.TOTAL
    ai_level: str = "medio"
    author: str = ""
    image_bytes: bytes | None = None

    @property
    def is_scenario(self) -> bool:
        """Un `.wom` cuenta como escenario (no solo mapa) si tiene título."""
        return bool(self.title.strip())


def save_scenario(doc: ScenarioDoc, name: str | None = None, directory: Path | None = None) -> Path:
    """Empaqueta el documento en <directory>/<nombre>.wom y devuelve la ruta."""
    directory = directory or MAPS_DIR
    directory.mkdir(parents=True, exist_ok=True)
    if name is None:
        name = _slugify(doc.title) or datetime.now().strftime("mapa_%Y%m%d_%H%M%S")
    payload = {
        "format_version": SCENARIO_FORMAT_VERSION,
        "saved_at": datetime.now().isoformat(timespec="seconds"),
        "meta": {
            "title": doc.title,
            "description": doc.description,
            "victory_mode": doc.victory_mode.value,
            "ai_level": doc.ai_level,
            "author": doc.author,
            "has_image": doc.image_bytes is not None,
        },
        "setup": {
            "world": doc.world.to_dict(),
            "armies": doc.army_specs,
            "players": [p.to_dict() for p in doc.players],
        },
    }
    path = directory / f"{name}{SCENARIO_EXT}"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(JSON_NAME, json.dumps(payload, ensure_ascii=False))
        if doc.image_bytes is not None:
            zf.writestr(IMAGE_NAME, doc.image_bytes)
    return path


def load_scenario(path: Path) -> ScenarioDoc:
    """Reconstruye el documento completo (con la imagen, si la trae)."""
    with zipfile.ZipFile(Path(path), "r") as zf:
        payload = json.loads(zf.read(JSON_NAME).decode("utf-8"))
        _check_version(payload)
        image = zf.read(IMAGE_NAME) if IMAGE_NAME in zf.namelist() else None
    meta = payload.get("meta", {})
    setup = payload["setup"]
    return ScenarioDoc(
        world=WorldMap.from_dict(setup["world"]),
        players=[Player.from_dict(p) for p in setup["players"]],
        army_specs=[dict(a) for a in setup.get("armies", [])],
        title=meta.get("title", ""),
        description=meta.get("description", ""),
        victory_mode=VictoryMode(meta.get("victory_mode", "total")),
        ai_level=meta.get("ai_level", "medio"),
        author=meta.get("author", ""),
        image_bytes=image,
    )


def scenario_info(path: Path) -> dict:
    """Resumen liviano para listar en el menú (lee solo el JSON, sin imagen)."""
    path = Path(path)
    with zipfile.ZipFile(path, "r") as zf:
        payload = json.loads(zf.read(JSON_NAME).decode("utf-8"))
    meta = payload.get("meta", {})
    return {
        "name": path.stem,
        "path": path,
        "title": meta.get("title", ""),
        "description": meta.get("description", ""),
        "victory_mode": meta.get("victory_mode", "total"),
        "ai_level": meta.get("ai_level", "medio"),
        "has_image": meta.get("has_image", False),
    }


def list_maps(directories: list[Path] | None = None) -> list[Path]:
    """Archivos `.wom` de las carpetas dadas, más reciente primero.

    Default: los escenarios distribuidos + los del usuario. Salta carpetas
    inexistentes y descarta duplicados por nombre.
    """
    directories = directories or [BUNDLED_SCENARIOS_DIR, MAPS_DIR]
    found: dict[str, Path] = {}
    for directory in directories:
        if not directory.exists():
            continue
        for path in directory.glob(f"*{SCENARIO_EXT}"):
            found.setdefault(path.name, path)
    return sorted(found.values(), key=lambda p: p.stat().st_mtime, reverse=True)


def build_game(
    doc: ScenarioDoc,
    *,
    players: list[Player] | None = None,
    victory_mode: VictoryMode | None = None,
) -> Game:
    """Arranca una partida turno-0 desde el documento.

    Sin overrides es un escenario completo: usa los jugadores (con su nivel de
    IA) y la victoria que trae el `.wom`. Con overrides es "Cargar mapa": el
    menú decide jugadores y modo de victoria y solo se reusa el terreno+tropas.
    """
    return Game.from_setup(
        doc.world,
        players if players is not None else doc.players,
        doc.army_specs,
        victory_mode if victory_mode is not None else doc.victory_mode,
    )


def _check_version(payload: dict) -> None:
    version = payload.get("format_version")
    if version != SCENARIO_FORMAT_VERSION:
        raise ValueError(
            f"mapa con formato v{version}; esta versión usa v{SCENARIO_FORMAT_VERSION}"
        )


def _slugify(text: str) -> str:
    """Nombre de archivo seguro a partir del título (vacío si no queda nada)."""
    slug = re.sub(r"[^\w\-]+", "_", text.strip().lower(), flags=re.UNICODE).strip("_")
    return slug[:60]
