"""Portapapeles del sistema (pygame.scrap) con degradación silenciosa.

Helpers compartidos por los campos de texto (pegar API keys) y la consola del
LLM (copiar el log). Sin display o sin soporte de clipboard (headless) degradan
a no-op, así los tests y las capturas no se rompen.
"""

from __future__ import annotations

import pygame


def clipboard_get() -> str:
    """Texto del portapapeles del sistema, o "" si no hay/no se puede."""
    try:
        return pygame.scrap.get_text() or ""
    except Exception:
        return ""


def clipboard_put(text: str) -> None:
    """Copia `text` al portapapeles del sistema (no-op si no se puede)."""
    try:
        pygame.scrap.put_text(text)
    except Exception:
        pass
