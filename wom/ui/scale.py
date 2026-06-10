"""Escalado de la resolución lógica del juego a la ventana física.

El juego renderiza siempre a theme.WINDOW_SIZE en un canvas lógico; cada
frame `present()` lo escala a la ventana manteniendo la proporción (bandas
negras si la relación no coincide). Para que el input siga funcionando, la
app traduce el `pos` de los eventos de mouse con `translate_event()` y los
hover de los botones (que leen el mouse directo) usan `mouse_pos()` en vez
de pygame.mouse.get_pos().

Sin escalado (ventana == resolución lógica) todo es identidad, así los
tests dibujan y clickean directo sin pasar por acá.
"""

from __future__ import annotations

import pygame

from wom.ui import theme

_scale = 1.0
_offset = (0, 0)

_MOUSE_EVENTS = (pygame.MOUSEBUTTONDOWN, pygame.MOUSEBUTTONUP, pygame.MOUSEMOTION)


def update(window_size: tuple[int, int]) -> None:
    """Recalcula escala y bandas para un tamaño de ventana dado."""
    global _scale, _offset
    logical_w, logical_h = theme.WINDOW_SIZE
    _scale = min(window_size[0] / logical_w, window_size[1] / logical_h)
    dest = (round(logical_w * _scale), round(logical_h * _scale))
    _offset = ((window_size[0] - dest[0]) // 2, (window_size[1] - dest[1]) // 2)


def to_logical(pos: tuple[int, int]) -> tuple[int, int]:
    """Punto físico de la ventana → coordenadas lógicas del juego."""
    return (
        round((pos[0] - _offset[0]) / _scale),
        round((pos[1] - _offset[1]) / _scale),
    )


def mouse_pos() -> tuple[int, int]:
    """Posición del mouse en coordenadas lógicas (para los hover)."""
    return to_logical(pygame.mouse.get_pos())


def translate_event(event: pygame.event.Event) -> pygame.event.Event:
    """Traduce el `pos` de los eventos de mouse a coordenadas lógicas."""
    if event.type not in _MOUSE_EVENTS or _scale == 1.0 and _offset == (0, 0):
        return event
    data = dict(event.dict)
    data["pos"] = to_logical(event.pos)
    return pygame.event.Event(event.type, data)


def present(window: pygame.Surface, canvas: pygame.Surface) -> None:
    """Escala el canvas lógico a la ventana y actualiza el transform."""
    update(window.get_size())
    if window.get_size() == canvas.get_size():
        window.blit(canvas, (0, 0))
        return
    dest = canvas.get_rect(topleft=(0, 0))
    dest.size = (round(dest.width * _scale), round(dest.height * _scale))
    window.fill((0, 0, 0))
    window.blit(pygame.transform.smoothscale(canvas, dest.size), _offset)
