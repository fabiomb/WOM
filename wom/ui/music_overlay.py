"""Ventana modal del reproductor de música (tecla M, en menú y en partida).

El loop de la app le ofrece cada evento antes que a la pantalla activa:
la M lo abre/cierra y, mientras está visible, consume todo el input (modal).
Controles: anterior / play-pausa / stop / siguiente, volumen -/+; también
por teclado (flechas, espacio, +/-).
"""

from __future__ import annotations

import pygame

from wom.ui import theme
from wom.ui.music import MusicPlayer

BOX_SIZE = (480, 240)


class MusicOverlay:
    def __init__(self, player: MusicPlayer):
        self.player = player
        self.visible = False
        self.font = pygame.font.SysFont(None, 28)
        self.small_font = pygame.font.SysFont(None, 20)
        self._buttons: dict[str, pygame.Rect] = {}

    # --- input ---------------------------------------------------------------

    def handle_event(self, event: pygame.event.Event) -> bool:
        """Devuelve True si consumió el evento (M, o cualquiera si está abierto)."""
        if event.type == pygame.KEYDOWN and event.key == pygame.K_m:
            self.visible = not self.visible
            return True
        if not self.visible:
            return False
        if event.type == pygame.KEYDOWN:
            if event.key == pygame.K_ESCAPE:
                self.visible = False
            elif event.key == pygame.K_SPACE:
                self.player.toggle_pause()
            elif event.key == pygame.K_RIGHT:
                self.player.next()
            elif event.key == pygame.K_LEFT:
                self.player.prev()
            elif event.key in (pygame.K_PLUS, pygame.K_KP_PLUS, pygame.K_UP):
                self.player.set_volume(self.player.settings.music_volume + 0.1)
            elif event.key in (pygame.K_MINUS, pygame.K_KP_MINUS, pygame.K_DOWN):
                self.player.set_volume(self.player.settings.music_volume - 0.1)
        elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            self._click(event.pos)
        return True  # modal: nada pasa a la pantalla de abajo

    def _click(self, point: tuple[int, int]) -> None:
        actions = {
            "prev": self.player.prev,
            "play": self.player.toggle_pause,
            "stop": self.player.stop,
            "next": self.player.next,
            "vol_down": lambda: self.player.set_volume(
                self.player.settings.music_volume - 0.1
            ),
            "vol_up": lambda: self.player.set_volume(
                self.player.settings.music_volume + 0.1
            ),
            "close": lambda: setattr(self, "visible", False),
        }
        for bid, rect in self._buttons.items():
            if rect.collidepoint(point):
                actions[bid]()
                return

    # --- dibujo ----------------------------------------------------------------

    def draw(self, surface: pygame.Surface) -> None:
        if not self.visible:
            return
        overlay = pygame.Surface(surface.get_size(), pygame.SRCALPHA)
        overlay.fill(theme.GAMEOVER_BG)
        surface.blit(overlay, (0, 0))

        box = pygame.Rect(0, 0, *BOX_SIZE)
        box.center = surface.get_rect().center
        pygame.draw.rect(surface, theme.SIDEBAR_BG, box, border_radius=8)
        pygame.draw.rect(surface, theme.SELECTION, box, 2, border_radius=8)
        self._buttons.clear()

        title = self.font.render("Reproductor de música", True, theme.TEXT)
        surface.blit(title, title.get_rect(midtop=(box.centerx, box.y + 14)))

        player = self.player
        if player.current is not None:
            track = player.current.stem
            counter = f"{player.position + 1}/{len(player.order)}"
            if player.playing and not player.paused:
                state = "Reproduciendo"
            elif player.paused:
                state = "En pausa"
            else:
                state = "Detenido"
            info = f"{counter}  ·  {state}"
        else:
            track = "(no hay archivos en la carpeta)"
            info = str(player.folder_path())
        name = self.font.render(_shorten(track, 34), True, theme.SELECTION)
        detail = self.small_font.render(_shorten(info, 52), True, theme.TEXT_DIM)
        surface.blit(name, name.get_rect(midtop=(box.centerx, box.y + 52)))
        surface.blit(detail, detail.get_rect(midtop=(box.centerx, box.y + 82)))

        playing = player.playing and not player.paused
        controls = (
            ("prev", "|<<"),
            ("play", "||" if playing else ">"),
            ("stop", "[ ]"),
            ("next", ">>|"),
        )
        button_w, gap = 70, 14
        x = box.centerx - (len(controls) * button_w + (len(controls) - 1) * gap) // 2
        for bid, label in controls:
            rect = pygame.Rect(x, box.y + 112, button_w, 40)
            self._draw_button(surface, bid, label, rect)
            x = rect.right + gap

        volume = round(player.settings.music_volume * 100)
        vol_label = self.small_font.render(f"Volumen {volume}%", True, theme.TEXT)
        vol_rect = vol_label.get_rect(center=(box.centerx, box.y + 184))
        surface.blit(vol_label, vol_rect)
        self._draw_button(
            surface, "vol_down", "-", pygame.Rect(vol_rect.left - 50, box.y + 168, 34, 32)
        )
        self._draw_button(
            surface, "vol_up", "+", pygame.Rect(vol_rect.right + 16, box.y + 168, 34, 32)
        )

        hint = self.small_font.render(
            "M / ESC cierra · espacio pausa · flechas cambian tema", True, theme.TEXT_DIM
        )
        surface.blit(hint, hint.get_rect(midbottom=(box.centerx, box.bottom - 10)))
        self._buttons["close"] = pygame.Rect(box.right - 30, box.y + 8, 22, 22)
        close = self.font.render("x", True, theme.TEXT_DIM)
        surface.blit(close, close.get_rect(center=self._buttons["close"].center))

    def _draw_button(
        self, surface: pygame.Surface, bid: str, label: str, rect: pygame.Rect
    ) -> None:
        over = rect.collidepoint(pygame.mouse.get_pos())
        pygame.draw.rect(
            surface, (70, 78, 86) if over else (50, 56, 62), rect, border_radius=6
        )
        text = self.font.render(label, True, theme.TEXT)
        surface.blit(text, text.get_rect(center=rect.center))
        self._buttons[bid] = rect


def _shorten(text: str, max_chars: int) -> str:
    return text if len(text) <= max_chars else text[: max_chars - 1] + "…"
