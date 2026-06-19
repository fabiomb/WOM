"""Modal de introducción al escenario, mostrado sobre el mapa al empezar.

Cuando se inicia un escenario (un `.wom` con título), esta ventana modal
aparece encima del mapa ya cargado y muestra su título, descripción e imagen,
para que el jugador sepa de qué va antes de jugar. Se cierra con un clic, Enter,
Espacio o ESC.

Es la contraparte in-game de la intro del menú (`menu_screen` → modo
`scenario_intro`): la misma información, pero ahora sobre el propio escenario.
Solo dibuja y maneja su cierre; no toca el estado de la partida.
"""

from __future__ import annotations

import io

import pygame

from wom.ui import scale, theme

PANEL = (28, 32, 38)
PANEL_BORDER = (90, 100, 110)
ACCENT = (250, 220, 60)


class ScenarioIntroOverlay:
    """Panel modal con título, descripción e imagen del escenario.

    `visible` arranca en True solo si hay algo que contar (un escenario siempre
    trae título; con un documento vacío el modal no molesta). La pantalla de
    juego le pasa los eventos mientras está visible y lo dibuja por encima de
    todo.
    """

    def __init__(
        self, title: str = "", description: str = "", image_bytes: bytes | None = None
    ):
        self.title = title.strip()
        self.description = description.strip()
        self.visible = bool(self.title or self.description or image_bytes)
        self.image: pygame.Surface | None = None
        if image_bytes:
            try:
                self.image = pygame.image.load(io.BytesIO(image_bytes))
            except (pygame.error, OSError):
                self.image = None
        self.title_font = pygame.font.SysFont(None, 60)
        self.font = pygame.font.SysFont(None, 32)
        self.small_font = pygame.font.SysFont(None, 26)
        self._button: pygame.Rect | None = None

    def handle_event(self, event: pygame.event.Event) -> bool:
        """Mientras está visible es modal: traga todo el input y un clic, Enter,
        Espacio o ESC lo cierran. Devuelve True si consumió el evento."""
        if not self.visible:
            return False
        if event.type == pygame.KEYDOWN and event.key in (
            pygame.K_RETURN, pygame.K_KP_ENTER, pygame.K_SPACE, pygame.K_ESCAPE
        ):
            self.visible = False
        elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            self.visible = False
        return True

    def draw(self, surface: pygame.Surface) -> None:
        if not self.visible:
            return
        overlay = pygame.Surface(surface.get_size(), pygame.SRCALPHA)
        overlay.fill(theme.GAMEOVER_BG)
        surface.blit(overlay, (0, 0))

        win = surface.get_rect()
        panel = pygame.Rect(0, 0, 1100, 820)
        panel.center = win.center
        pygame.draw.rect(surface, PANEL, panel, border_radius=12)
        pygame.draw.rect(surface, PANEL_BORDER, panel, 2, border_radius=12)

        inner = panel.width - 96
        y = panel.y + 40
        if self.title:
            title = self.title_font.render(self.title, True, ACCENT)
            surface.blit(title, title.get_rect(midtop=(panel.centerx, y)))
            y += title.get_height() + 24
        if self.image is not None:
            max_w, max_h = inner, 420
            iw, ih = self.image.get_width(), self.image.get_height()
            f = min(max_w / iw, max_h / ih, 1.0)
            scaled = pygame.transform.smoothscale(
                self.image, (round(iw * f), round(ih * f))
            )
            surface.blit(scaled, scaled.get_rect(midtop=(panel.centerx, y)))
            y += scaled.get_height() + 28
        self._wrap_text(surface, self.description, panel.centerx, y, inner)

        btn = pygame.Rect(0, 0, 300, 56)
        btn.center = (panel.centerx, panel.bottom - 66)
        over = btn.collidepoint(scale.mouse_pos())
        pygame.draw.rect(
            surface, theme.BUTTON_BG_OVER if over else theme.BUTTON_BG, btn,
            border_radius=8,
        )
        label = self.font.render("Comenzar", True, theme.TEXT)
        surface.blit(label, label.get_rect(center=btn.center))
        self._button = btn
        hint = self.small_font.render(
            "(clic, Enter o ESC para empezar)", True, theme.TEXT_DIM
        )
        surface.blit(hint, hint.get_rect(midbottom=(panel.centerx, panel.bottom - 10)))

    def _wrap_text(self, surface, text, cx, y, max_w) -> int:
        """Dibuja `text` centrado y ajustado a `max_w`; respeta saltos de línea."""
        if not text:
            return y
        for paragraph in text.split("\n"):
            line = ""
            for word in paragraph.split():
                probe = f"{line} {word}".strip()
                if self.font.size(probe)[0] > max_w and line:
                    rendered = self.font.render(line, True, theme.TEXT)
                    surface.blit(rendered, rendered.get_rect(midtop=(cx, y)))
                    y += 34
                    line = word
                else:
                    line = probe
            rendered = self.font.render(line, True, theme.TEXT)
            surface.blit(rendered, rendered.get_rect(midtop=(cx, y)))
            y += 34
        return y
