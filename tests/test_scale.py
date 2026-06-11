"""Tests del escalado lógico → ventana (wom/ui/scale.py).

Independientes de la resolución lógica concreta: todo se expresa relativo
a theme.WINDOW_SIZE.
"""

import os

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

import pygame
import pytest

from wom.ui import scale, theme

LOGICAL_W, LOGICAL_H = theme.WINDOW_SIZE


@pytest.fixture(autouse=True)
def transform_en_identidad():
    """Deja el transform global en identidad al salir de cada test."""
    yield
    scale.update(theme.WINDOW_SIZE)


def test_escala_doble():
    scale.update((LOGICAL_W * 2, LOGICAL_H * 2))  # 2x exacto, sin bandas
    assert scale.to_logical((LOGICAL_W * 2, LOGICAL_H * 2)) == (LOGICAL_W, LOGICAL_H)
    assert scale.to_logical((640, 360)) == (320, 180)


def test_bandas_laterales():
    scale.update((LOGICAL_W + 640, LOGICAL_H))  # escala 1.0, bandas de 320
    assert scale.to_logical((320, 0)) == (0, 0)
    assert scale.to_logical((320 + LOGICAL_W, LOGICAL_H)) == (LOGICAL_W, LOGICAL_H)


def test_translate_event():
    scale.update((LOGICAL_W * 2, LOGICAL_H * 2))
    click = pygame.event.Event(
        pygame.MOUSEBUTTONDOWN, {"pos": (200, 100), "button": 1}
    )
    translated = scale.translate_event(click)
    assert translated.pos == (100, 50)
    assert translated.button == 1  # el resto del evento se conserva
    key = pygame.event.Event(pygame.KEYDOWN, {"key": pygame.K_a})
    assert scale.translate_event(key) is key  # sin pos: pasa intacto


def test_translate_event_es_identidad_sin_escala():
    scale.update(theme.WINDOW_SIZE)
    click = pygame.event.Event(
        pygame.MOUSEBUTTONDOWN, {"pos": (200, 100), "button": 1}
    )
    assert scale.translate_event(click) is click


def test_present_escala_con_bandas():
    canvas = pygame.Surface(theme.WINDOW_SIZE)
    canvas.fill((200, 0, 0))
    window = pygame.Surface((LOGICAL_W + 640, LOGICAL_H))  # más ancha
    window.fill((1, 2, 3))  # debe quedar tapado por bandas + contenido
    scale.present(window, canvas)
    assert window.get_at((0, LOGICAL_H // 2))[:3] == (0, 0, 0)  # banda izquierda
    center = window.get_at(((LOGICAL_W + 640) // 2, LOGICAL_H // 2))
    assert center[0] > 150 and center[1] == 0  # centro: contenido rojo
    assert window.get_at((LOGICAL_W + 639, LOGICAL_H // 2))[:3] == (0, 0, 0)


def test_present_sin_escala_copia_directo():
    canvas = pygame.Surface(theme.WINDOW_SIZE)
    canvas.fill((0, 200, 0))
    window = pygame.Surface(theme.WINDOW_SIZE)
    scale.present(window, canvas)
    assert window.get_at((LOGICAL_W // 2, LOGICAL_H // 2))[:3] == (0, 200, 0)
    assert scale.to_logical((100, 100)) == (100, 100)  # identidad