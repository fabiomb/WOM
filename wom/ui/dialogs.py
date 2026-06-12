"""Diálogo modal de selección de tropas por clase (dividir y fusionar).

`TroopPicker` muestra una fila por clase presente con botones - / + (con
Shift van de a 10), atajos "Todo"/"Nada" y Aceptar/Cancelar. Respeta dos
límites: lo disponible por clase y un tope global `cap` (para fusionar,
el lugar libre del ejército destino; para dividir, el total menos una
tropa que debe quedar en el original).

Como los demás modales del juego, el hit-test usa los rects del último
draw: hay que dibujar antes de poder clickear (los tests ya siguen ese
patrón). El llamador interpreta el resultado de handle_event: "accept",
"cancel" o None.
"""

from __future__ import annotations

import pygame

from wom.ui import scale, theme

ROW_HEIGHT = 44
BOX_WIDTH = 560
STEP_FAST = 10  # paso de los botones - / + con Shift apretado


class TroopPicker:
    def __init__(
        self,
        title: str,
        subtitle: str,
        rows: list[tuple[str, str, int]],
        cap: int,
        initial: dict[str, int] | None = None,
        accept_label: str = "Aceptar (S)",
    ):
        self.title = title
        self.subtitle = subtitle
        self.rows = rows  # (class_id, nombre visible, disponible)
        self.cap = max(0, cap)
        self.accept_label = accept_label
        self.amounts: dict[str, int] = {cid: 0 for cid, _, _ in rows}
        if initial:
            for cid, _, available in rows:
                self.amounts[cid] = min(max(0, initial.get(cid, 0)), available)
            self._enforce_cap()
        self.font = pygame.font.SysFont(None, 30)
        self.small_font = pygame.font.SysFont(None, 22)
        self._buttons: dict[str, pygame.Rect] = {}

    @property
    def total(self) -> int:
        return sum(self.amounts.values())

    def set_all(self, full: bool) -> None:
        """"Todo": llena cada clase hasta lo disponible (respetando el tope
        global, en orden de filas). "Nada": todo a cero."""
        remaining = self.cap if full else 0
        for cid, _, available in self.rows:
            take = min(available, remaining)
            self.amounts[cid] = take
            remaining -= take

    def adjust(self, class_id: str, delta: int) -> None:
        available = next(a for cid, _, a in self.rows if cid == class_id)
        room = self.cap - (self.total - self.amounts[class_id])
        self.amounts[class_id] = min(
            max(0, self.amounts[class_id] + delta), available, room
        )

    def _enforce_cap(self) -> None:
        excess = self.total - self.cap
        for cid, _, _ in reversed(self.rows):
            if excess <= 0:
                break
            cut = min(self.amounts[cid], excess)
            self.amounts[cid] -= cut
            excess -= cut

    # --- input -------------------------------------------------------------

    def handle_event(self, event: pygame.event.Event) -> str | None:
        """Devuelve "accept", "cancel" o None. Aceptar requiere total > 0."""
        if event.type == pygame.KEYDOWN:
            if event.key in (pygame.K_s, pygame.K_RETURN, pygame.K_KP_ENTER):
                return "accept" if self.total > 0 else None
            if event.key in (pygame.K_n, pygame.K_ESCAPE):
                return "cancel"
            if event.key == pygame.K_t:
                self.set_all(True)
            return None
        if event.type != pygame.MOUSEBUTTONDOWN or event.button != 1:
            return None
        step = STEP_FAST if pygame.key.get_mods() & pygame.KMOD_SHIFT else 1
        for key, rect in self._buttons.items():
            if not rect.collidepoint(event.pos):
                continue
            if key == "accept":
                return "accept" if self.total > 0 else None
            if key == "cancel":
                return "cancel"
            if key == "all":
                self.set_all(True)
            elif key == "none":
                self.set_all(False)
            elif key.startswith("minus:"):
                self.adjust(key.split(":", 1)[1], -step)
            elif key.startswith("plus:"):
                self.adjust(key.split(":", 1)[1], step)
            return None
        return None

    # --- dibujo ------------------------------------------------------------

    def draw(self, surface: pygame.Surface) -> None:
        overlay = pygame.Surface(surface.get_size(), pygame.SRCALPHA)
        overlay.fill(theme.GAMEOVER_BG)
        surface.blit(overlay, (0, 0))

        box = pygame.Rect(0, 0, BOX_WIDTH, 176 + len(self.rows) * ROW_HEIGHT)
        box.center = surface.get_rect().center
        pygame.draw.rect(surface, theme.SIDEBAR_BG, box, border_radius=8)
        pygame.draw.rect(surface, theme.SELECTION, box, 2, border_radius=8)
        self._buttons = {}
        mouse = scale.mouse_pos()

        title = self.font.render(self.title, True, theme.TEXT)
        surface.blit(title, title.get_rect(midtop=(box.centerx, box.y + 16)))
        subtitle = self.small_font.render(self.subtitle, True, theme.TEXT_DIM)
        surface.blit(subtitle, subtitle.get_rect(midtop=(box.centerx, box.y + 46)))

        y = box.y + 74
        for cid, label, available in self.rows:
            name = self.small_font.render(f"{label} ({available})", True, theme.TEXT)
            surface.blit(name, (box.x + 28, y + 8))
            plus = pygame.Rect(box.right - 64, y + 2, 36, 30)
            minus = pygame.Rect(box.right - 188, y + 2, 36, 30)
            for key, rect, sign in ((f"minus:{cid}", minus, "-"), (f"plus:{cid}", plus, "+")):
                over = rect.collidepoint(mouse)
                pygame.draw.rect(
                    surface, (70, 78, 86) if over else (50, 56, 62), rect,
                    border_radius=6,
                )
                glyph = self.font.render(sign, True, theme.TEXT)
                surface.blit(glyph, glyph.get_rect(center=rect.center))
                self._buttons[key] = rect
            count = self.font.render(str(self.amounts[cid]), True, theme.SELECTION)
            mid_x = (minus.right + plus.left) // 2
            surface.blit(count, count.get_rect(center=(mid_x, minus.centery)))
            y += ROW_HEIGHT

        # Atajos Todo / Nada y total elegido.
        y += 4
        for key, label, x in (("all", "Todo (T)", box.x + 28), ("none", "Nada", box.x + 138)):
            rect = pygame.Rect(x, y, 100, 28)
            over = rect.collidepoint(mouse)
            pygame.draw.rect(
                surface, (70, 78, 86) if over else (50, 56, 62), rect, border_radius=6
            )
            text = self.small_font.render(label, True, theme.TEXT)
            surface.blit(text, text.get_rect(center=rect.center))
            self._buttons[key] = rect
        total = self.small_font.render(
            f"Elegidas: {self.total} / {self.cap}", True, theme.TEXT
        )
        surface.blit(total, total.get_rect(midright=(box.right - 28, y + 14)))

        yes = pygame.Rect(box.x + 28, box.bottom - 52, 220, 38)
        no = pygame.Rect(box.right - 248, box.bottom - 52, 220, 38)
        for key, rect, label, base, hover in (
            ("accept", yes, self.accept_label, theme.BUTTON_BG, theme.BUTTON_BG_OVER),
            ("cancel", no, "Cancelar (N)", (50, 56, 62), (70, 78, 86)),
        ):
            color = hover if rect.collidepoint(mouse) else base
            pygame.draw.rect(surface, color, rect, border_radius=6)
            text = self.font.render(label, True, theme.TEXT)
            surface.blit(text, text.get_rect(center=rect.center))
            self._buttons[key] = rect
