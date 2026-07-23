"""Consola del LLM (F2): ajuste de línea puro, scroll y dibujo headless."""

import os
import time
from types import SimpleNamespace

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

import pygame
import pytest

from wom.ui import theme
from wom.ui.console_overlay import ConsoleOverlay, wrap_line


@pytest.fixture(scope="module")
def screen():
    pygame.init()
    yield pygame.display.set_mode(theme.WINDOW_SIZE)
    pygame.quit()


def test_wrap_line_corta_por_palabras():
    assert wrap_line("uno dos tres", 8) == ["uno dos", "tres"]
    assert wrap_line("corto", 20) == ["corto"]
    assert wrap_line("", 10) == [""]


def test_wrap_line_parte_palabras_largas():
    lines = wrap_line("supercalifragilistico", 8)
    assert all(len(line) <= 8 for line in lines)
    assert "".join(lines) == "supercalifragilistico"


def _runner_stub(n_lines: int = 3):
    now = time.time()
    kinds = ["info", "raw", "ok", "warn", "error", "chat"]
    lines = [
        (now, kinds[i % len(kinds)], f"entrada {i} " + "x" * 80) for i in range(n_lines)
    ]
    return SimpleNamespace(name="Bot", config=None, log_lines=lines)


def test_draw_y_toggle(screen):
    console = ConsoleOverlay()
    runner = _runner_stub()
    console.draw(screen, runner)  # invisible: no dibuja ni rompe
    console.toggle()
    assert console.visible
    console.draw(screen, runner)  # visible con entradas de todos los tipos
    console.draw(screen, None)  # sin runner: no rompe
    console.toggle()
    assert not console.visible


def test_scroll_consume_pgup_solo_visible(screen):
    console = ConsoleOverlay()
    runner = _runner_stub(n_lines=100)
    ev = pygame.event.Event(pygame.KEYDOWN, {"key": pygame.K_PAGEUP, "unicode": ""})
    assert console.handle_event(ev) is False  # cerrada: no consume
    console.toggle()
    console.draw(screen, runner)  # calcula el total de líneas envueltas
    assert console.handle_event(ev) is True
    assert console.scroll > 0
    down = pygame.event.Event(pygame.KEYDOWN, {"key": pygame.K_PAGEDOWN, "unicode": ""})
    while console.handle_event(down) and console.scroll:
        pass
    assert console.scroll == 0
    # Otras teclas no se consumen (el juego sigue recibiendo el input).
    other = pygame.event.Event(pygame.KEYDOWN, {"key": pygame.K_a, "unicode": "a"})
    assert console.handle_event(other) is False


def test_reabrir_vuelve_al_presente(screen):
    console = ConsoleOverlay()
    console.toggle()
    console.draw(screen, _runner_stub(50))
    console.scroll = 20
    console.toggle()
    console.toggle()
    assert console.scroll == 0


def test_p_alterna_los_prompts(screen):
    console = ConsoleOverlay()
    console.toggle()
    now = time.time()
    runner = SimpleNamespace(
        name="Bot", config=None,
        log_lines=[(now, "prompt", "Observación:\n   0123\n 0 .A.."), (now, "ok", "1 órdenes")],
    )
    console.draw(screen, runner)
    with_prompts = console._wrapped
    ev = pygame.event.Event(pygame.KEYDOWN, {"key": pygame.K_p, "mod": 0, "unicode": "p"})
    assert console.handle_event(ev) is True
    assert console.show_prompts is False
    console.draw(screen, runner)
    assert console._wrapped < with_prompts, "ocultar prompts achica el log"


def test_wrap_respeta_lineas_del_mapa_ascii():
    console = ConsoleOverlay()
    entries = [(time.time(), "prompt", "   0123456789\n 0 .A~~\n10 =...")]
    lines = [text for text, _color in console._wrap_entries(entries, 80)]
    # Las líneas cortas pasan intactas (alineación del mapa), con sangría fija.
    assert lines[0].endswith("   0123456789")
    assert lines[1] == "          0 .A~~"
    assert lines[2] == "         10 =..."


def test_ctrl_c_copia_el_log(screen, monkeypatch):
    import wom.ui.console_overlay as co

    copied = {}
    monkeypatch.setattr(co, "clipboard_put", lambda t: copied.setdefault("v", t))
    console = ConsoleOverlay()
    console.toggle()
    console.draw(screen, _runner_stub(3))
    ev = pygame.event.Event(
        pygame.KEYDOWN, {"key": pygame.K_c, "mod": pygame.KMOD_CTRL, "unicode": "\x03"}
    )
    assert console.handle_event(ev) is True
    assert "entrada 0" in copied["v"] and "[raw]" in copied["v"]
    # Sin Ctrl, la C no se consume (es una tecla del juego).
    plain = pygame.event.Event(pygame.KEYDOWN, {"key": pygame.K_c, "mod": 0, "unicode": "c"})
    assert console.handle_event(plain) is False
