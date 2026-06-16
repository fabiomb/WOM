"""Huella (hash) de la configuración de balance, para el handshake.

El lockstep exige que ambos clientes tengan la **misma** config que afecta la
simulación (`classes.json` y `game.json`: clases, umbrales de batalla, costos).
En el handshake, el host compara su huella con la del cliente; si difieren, se
rechaza la conexión con un motivo claro en vez de divergir en silencio turnos
después.

`ai.json` no entra: en una partida en red no hay AI, así que no influye en el
determinismo del juego.
"""

from __future__ import annotations

import hashlib

from wom.core.config import CONFIG_DIR

# Solo los archivos que afectan la simulación (ver docstring).
_RELEVANT_FILES = ("classes.json", "game.json")


def config_fingerprint() -> str:
    """SHA-1 hex del contenido de los configs relevantes para el determinismo."""
    digest = hashlib.sha1()
    for name in _RELEVANT_FILES:
        digest.update(name.encode("utf-8"))
        digest.update(b"\0")
        digest.update((CONFIG_DIR / name).read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()
