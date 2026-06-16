"""Huella (hash) determinista del estado de una partida.

En el lockstep, tras resolver cada turno ambos clientes intercambian este
digest para verificar que sus simulaciones siguen sincronizadas. Si difieren,
el host reenvía el estado completo (`STATE_SYNC`).

El digest se calcula sobre `Game.to_dict()` con las claves ordenadas: dos
partidas son idénticas si y solo si su `to_dict()` coincide (es el mismo
criterio del test de continuación determinista del savegame), así que esta es
la proyección canónica más fiel y barata.
"""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from wom.core.game import Game


def state_digest(game: "Game") -> str:
    """SHA-1 hex del estado completo del juego (determinista entre máquinas)."""
    blob = json.dumps(game.to_dict(), sort_keys=True, separators=(",", ":"))
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()
