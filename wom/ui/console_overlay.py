"""Consola del rival LLM (F2): log en vivo de sus respuestas y decisiones.

Panel translúcido sobre el mapa que muestra, a medida que ocurren, las entradas
del `LLMRunner.log_lines` — respuesta cruda del modelo, órdenes traducidas,
acciones descartadas (con motivo), chat y errores — para poder evaluar cómo
juega cada modelo sin salir de la partida.

No es un modal: el juego sigue recibiendo el input normalmente. La consola solo
consume sus propias teclas — PgUp/PgDn y la rueda sobre el panel (desplazarse),
**P** (mostrar/ocultar los prompts enviados al modelo, largos por el mapa ASCII)
y **Ctrl+C** (copiar el log completo al portapapeles, para compartir ejemplos) —
y F2 la abre/cierra (eso lo maneja `GameScreen`, que sabe si hay un runner). El
ajuste de línea es una función pura (`wrap_line`), testeable sin pygame; las
entradas multilínea (el mapa ASCII del prompt) conservan sus saltos de línea.
"""

from __future__ import annotations

import time

import pygame

from wom.ui import scale
from wom.ui.clipboard import clipboard_put

# Colores por tipo de entrada (fondo oscuro).
KIND_COLORS = {
    "info": (200, 200, 200),
    "prompt": (170, 160, 205),  # observación enviada al modelo
    "raw": (150, 175, 200),     # respuesta cruda del modelo
    "ok": (120, 200, 120),      # órdenes aceptadas
    "warn": (235, 205, 90),     # descartes / avisos
    "error": (220, 90, 90),
    "chat": (140, 190, 235),
}
PANEL_BG = (12, 14, 18, 225)
BORDER = (90, 96, 104)
TITLE_COLOR = (235, 225, 200)
PANEL_WIDTH_FRAC = 0.56  # fracción del ancho de la ventana
MARGIN = 24
PAGE_LINES = 10  # cuánto desplaza PgUp/PgDn


def wrap_line(text: str, cols: int) -> list[str]:
    """Corta `text` en líneas de a lo sumo `cols` caracteres (fuente mono).

    Una línea que entra entera se devuelve **intacta** (conserva sus espacios:
    el mapa ASCII del prompt depende de la alineación). Si no entra, corta por
    palabras; una palabra más larga que la línea se parte. Siempre devuelve al
    menos una línea (puede ser vacía).
    """
    cols = max(1, cols)
    if len(text) <= cols:
        return [text]
    lines: list[str] = []
    current = ""
    for word in text.split(" "):
        while len(word) > cols:  # palabra más larga que la línea: se parte
            space = cols - len(current) - (1 if current else 0)
            if space > 0:
                current = (current + " " + word[:space]).strip()
            lines.append(current)
            word = word[space if space > 0 else 0:]
            current = ""
        probe = f"{current} {word}".strip() if current else word
        if len(probe) > cols and current:
            lines.append(current)
            current = word
        else:
            current = probe
    lines.append(current)
    return lines


class ConsoleOverlay:
    """Panel de consola sobre la partida. `draw` recibe el runner (o None)."""

    def __init__(self) -> None:
        self.visible = False
        self.scroll = 0  # líneas envueltas desde el fondo (0 = pegado al final)
        self.show_prompts = True  # P los oculta (son largos: mapa ASCII)
        self.font = pygame.font.SysFont("consolas,courier", 19)
        self.title_font = pygame.font.SysFont(None, 24)
        self._panel: pygame.Rect | None = None
        self._wrapped: int = 0  # total de líneas envueltas del último draw
        self._entries: list[tuple[float, str, str]] = []  # último log dibujado
        self._copied_until = 0.0  # feedback "copiado ✓" en el título

    def toggle(self) -> None:
        self.visible = not self.visible
        self.scroll = 0  # siempre reabre pegada al presente

    # --- input -------------------------------------------------------------

    def handle_event(self, event: pygame.event.Event) -> bool:
        """Consume solo sus teclas: scroll, P (prompts) y Ctrl+C (copiar)."""
        if not self.visible:
            return False
        if event.type == pygame.KEYDOWN:
            if event.key == pygame.K_PAGEUP:
                self._scroll_by(PAGE_LINES)
                return True
            if event.key == pygame.K_PAGEDOWN:
                self._scroll_by(-PAGE_LINES)
                return True
            if event.key == pygame.K_p:
                self.show_prompts = not self.show_prompts
                self.scroll = 0
                return True
            if event.key == pygame.K_c and getattr(event, "mod", 0) & pygame.KMOD_CTRL:
                self.copy_log()
                return True
        elif event.type == pygame.MOUSEWHEEL:
            panel = self._panel
            if panel is not None and panel.collidepoint(scale.mouse_pos()):
                self._scroll_by(event.y * 3)
                return True
        return False

    def _scroll_by(self, lines: int) -> None:
        self.scroll = max(0, min(self.scroll + lines, max(0, self._wrapped - 1)))

    def copy_log(self) -> None:
        """Copia el log completo (todos los tipos, con hora) al portapapeles."""
        text = "\n".join(
            f"{time.strftime('%H:%M:%S', time.localtime(ts))} [{kind}] {line}"
            for ts, kind, line in self._entries
        )
        clipboard_put(text)
        self._copied_until = time.time() + 2.0

    # --- dibujo ------------------------------------------------------------

    def draw(self, surface: pygame.Surface, runner) -> None:
        if not self.visible or runner is None:
            return
        window = surface.get_rect()
        panel = pygame.Rect(
            MARGIN, MARGIN,
            int(window.width * PANEL_WIDTH_FRAC), window.height - 2 * MARGIN,
        )
        self._panel = panel
        overlay = pygame.Surface(panel.size, pygame.SRCALPHA)
        overlay.fill(PANEL_BG)
        surface.blit(overlay, panel.topleft)
        pygame.draw.rect(surface, BORDER, panel, width=2, border_radius=8)

        title = self._title(runner)
        surface.blit(
            self.title_font.render(title, True, TITLE_COLOR), (panel.x + 12, panel.y + 8)
        )
        body = panel.inflate(-24, 0)
        body.y = panel.y + 36
        body.height = panel.bottom - 12 - body.y

        char_w = max(1, self.font.size("M")[0])
        cols = max(10, body.width // char_w)
        line_h = self.font.get_height() + 2
        max_lines = max(1, body.height // line_h)

        self._entries = list(runner.log_lines)
        wrapped = self._wrap_entries(self._entries, cols)
        self._wrapped = len(wrapped)
        self.scroll = max(0, min(self.scroll, max(0, self._wrapped - 1)))
        end = len(wrapped) - self.scroll
        visible = wrapped[max(0, end - max_lines):end]
        y = body.y
        for text, color in visible:
            surface.blit(self.font.render(text, True, color), (body.x, y))
            y += line_h
        if self.scroll > 0:
            tail = self.font.render(f"▼ {self.scroll} líneas más abajo", True, KIND_COLORS["warn"])
            surface.blit(tail, tail.get_rect(bottomright=(panel.right - 12, panel.bottom - 6)))

    def _title(self, runner) -> str:
        model = ""
        config = getattr(runner, "config", None)
        if config is not None:
            model = f" · {config.provider}/{config.model}"
        prompts = "P oculta prompts" if self.show_prompts else "P muestra prompts"
        copied = "  ·  copiado ✓" if time.time() < self._copied_until else ""
        return (
            f"Consola LLM — {runner.name}{model}   "
            f"(F2 cierra · PgUp/PgDn · {prompts} · Ctrl+C copia){copied}"
        )

    def _wrap_entries(
        self, entries: list[tuple[float, str, str]], cols: int
    ) -> list[tuple[str, tuple[int, int, int]]]:
        """Aplana el log en líneas (texto, color) con hora y sangría."""
        out: list[tuple[str, tuple[int, int, int]]] = []
        body_cols = max(10, cols - 9)  # "HH:MM:SS " = 9 columnas de sangría
        for ts, kind, text in entries:
            if kind == "prompt" and not self.show_prompts:
                continue
            color = KIND_COLORS.get(kind, KIND_COLORS["info"])
            stamp = time.strftime("%H:%M:%S", time.localtime(ts))
            # Los saltos de línea del texto se respetan (el mapa ASCII del
            # prompt los necesita); cada línea larga se envuelve aparte.
            lines = [
                piece
                for chunk in text.split("\n")
                for piece in wrap_line(chunk, body_cols)
            ]
            out.append((f"{stamp} {lines[0]}", color))
            out.extend((f"         {line}", color) for line in lines[1:])
        return out
