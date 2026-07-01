"""Huella (hash) de la configuración de balance, para el handshake.

El lockstep exige que ambos clientes tengan la **misma** config que afecta la
simulación (`classes.json` y `game.json`: clases, umbrales de batalla, costos).
En el handshake, el host compara su huella con la del cliente; si difieren, se
rechaza la conexión con un motivo claro en vez de divergir en silencio turnos
después.

La huella se calcula sobre el contenido **normalizado** de los archivos (fin de
línea `\r\n`/`\r` → `\n`): distintos checkouts pueden dejar los JSON en CRLF o
LF (Git con `core.autocrlf`), pero eso no cambia la simulación, así que no debe
cambiar la huella. Si no se normalizara, un cliente empaquetado con CRLF y un
servidor con LF —los mismos datos— se rechazarían mutuamente.

`ai.json` no entra: en una partida en red no hay AI, así que no influye en el
determinismo del juego.
"""

from __future__ import annotations

import hashlib

from wom.core.config import CONFIG_DIR

# Solo los archivos que afectan la simulación (ver docstring).
_RELEVANT_FILES = ("classes.json", "game.json")


def _normalized_bytes(path) -> bytes:
    """Contenido del archivo con los fines de línea normalizados a ``\\n``.

    Así la huella no depende de si el checkout dejó el JSON en CRLF o LF.
    """
    text = path.read_text(encoding="utf-8")
    return text.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8")


def config_fingerprint() -> str:
    """SHA-1 hex del contenido de los configs relevantes para el determinismo."""
    digest = hashlib.sha1()
    for name in _RELEVANT_FILES:
        digest.update(name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(_normalized_bytes(CONFIG_DIR / name))
        digest.update(b"\0")
    return digest.hexdigest()
