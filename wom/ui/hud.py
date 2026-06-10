"""Panel lateral: estado de la partida, ejército seleccionado, fin de turno."""

from __future__ import annotations

import pygame

from wom.core.army import Army
from wom.core.game import Game
from wom.core.victory import VictoryResult
from wom.core.worldmap import Fort
from wom.ui import theme


class Hud:
    def __init__(self, rect: pygame.Rect):
        self.rect = rect
        self.title_font = pygame.font.SysFont(None, 34)
        self.font = pygame.font.SysFont(None, 22)
        self.small_font = pygame.font.SysFont(None, 18)
        self.button = pygame.Rect(
            rect.x + 20, rect.bottom - 60, rect.width - 40, 42
        )
        self.create_button = pygame.Rect(
            rect.x + 20, rect.bottom - 115, rect.width - 40, 42
        )
        self._create_button_visible = False

    def hit_end_turn(self, point: tuple[int, int]) -> bool:
        return self.button.collidepoint(point)

    def hit_create_army(self, point: tuple[int, int]) -> bool:
        return self._create_button_visible and self.create_button.collidepoint(point)

    def draw(
        self,
        surface: pygame.Surface,
        game: Game,
        selected: Army | None,
        selected_fort: Fort | None = None,
        creation_pending: bool = False,
        result: VictoryResult | None = None,
    ) -> None:
        pygame.draw.rect(surface, theme.SIDEBAR_BG, self.rect)
        x = self.rect.x + 20
        y = self.rect.y + 16
        y = self._text(surface, f"WOM — turno {game.turn + 1}", x, y, self.title_font)
        y += 8

        for player in game.players:
            color = theme.player_color(player.id)
            armies = game.armies_of(player.id)
            troops = sum(a.total_troops for a in armies)
            forts = sum(1 for f in game.world.forts if f.owner == player.id)
            towns = sum(1 for t in game.world.towns if t.owner == player.id)
            y = self._text(surface, player.name, x, y, self.font, color)
            y = self._text(
                surface, f"  {len(armies)} ejércitos · {troops} tropas",
                x, y, self.small_font, theme.TEXT_DIM,
            )
            y = self._text(
                surface,
                f"  {forts} fuertes · {towns} pueblos · comida {player.food}",
                x, y, self.small_font, theme.TEXT_DIM,
            )
            y += 6

        if game.last_battles:
            y += 6
            y = self._text(
                surface, f"Batallas el turno pasado: {len(game.last_battles)}",
                x, y, self.font,
            )

        if selected is not None:
            y += 14
            y = self._text(surface, "Ejército seleccionado", x, y, self.font, theme.SELECTION)
            speed = selected.speed(game.classes)
            y = self._text(
                surface,
                f"  XP {selected.xp} · comida {selected.food} · velocidad {speed}",
                x, y, self.small_font, theme.TEXT_DIM,
            )
            for class_id, count in sorted(selected.composition.items()):
                if count > 0:
                    name = game.classes[class_id].nombre
                    y = self._text(surface, f"  {name}: {count}", x, y, self.small_font)
            y += 4
            y = self._text(
                surface, "Click en el mapa: trazar camino", x, y,
                self.small_font, theme.TEXT_DIM,
            )
            y = self._text(
                surface, "Click derecho: borrar/deseleccionar", x, y,
                self.small_font, theme.TEXT_DIM,
            )
        elif selected_fort is not None:
            y += 14
            y = self._text(surface, "Fuerte seleccionado", x, y, self.font, theme.SELECTION)
            y = self._text(
                surface, f"  Reserva: {selected_fort.reserve_total} tropas",
                x, y, self.small_font, theme.TEXT_DIM,
            )
            for class_id, count in sorted(selected_fort.reserve.items()):
                if count > 0:
                    name = game.classes[class_id].nombre
                    y = self._text(surface, f"  {name}: {count}", x, y, self.small_font)
        else:
            y += 14
            y = self._text(
                surface, "Click en un ejército propio para darle órdenes",
                x, y, self.small_font, theme.TEXT_DIM,
            )
            y = self._text(
                surface, "Click en un fuerte propio para crear ejércitos",
                x, y, self.small_font, theme.TEXT_DIM,
            )

        if result is not None and result.is_over:
            self._create_button_visible = False
            self._draw_game_over(surface, game, result)
        else:
            self._create_button_visible = (
                selected_fort is not None and selected_fort.reserve_total > 0
            )
            if self._create_button_visible:
                over = self.create_button.collidepoint(pygame.mouse.get_pos())
                pygame.draw.rect(
                    surface, theme.BUTTON_BG_OVER if over else theme.BUTTON_BG,
                    self.create_button, border_radius=6,
                )
                text = "Cancelar creación" if creation_pending else "Crear ejército"
                label = self.font.render(text, True, theme.TEXT)
                surface.blit(label, label.get_rect(center=self.create_button.center))
            over = self.button.collidepoint(pygame.mouse.get_pos())
            pygame.draw.rect(
                surface, theme.BUTTON_BG_OVER if over else theme.BUTTON_BG,
                self.button, border_radius=6,
            )
            label = self.font.render("Fin del turno (Enter)", True, theme.TEXT)
            surface.blit(label, label.get_rect(center=self.button.center))

    def _draw_game_over(
        self, surface: pygame.Surface, game: Game, result: VictoryResult
    ) -> None:
        overlay = pygame.Surface(surface.get_size(), pygame.SRCALPHA)
        overlay.fill(theme.GAMEOVER_BG)
        surface.blit(overlay, (0, 0))
        if result.winner is not None:
            text = f"Ganó {game.players[result.winner].name}"
            color = theme.player_color(result.winner)
        else:
            text, color = "Empate", theme.TEXT
        center = surface.get_rect().center
        title = self.title_font.render(text, True, color)
        reason = self.font.render(result.reason, True, theme.TEXT)
        hint = self.small_font.render("ESC para salir", True, theme.TEXT_DIM)
        surface.blit(title, title.get_rect(center=(center[0], center[1] - 30)))
        surface.blit(reason, reason.get_rect(center=center))
        surface.blit(hint, hint.get_rect(center=(center[0], center[1] + 30)))

    def _text(self, surface, text, x, y, font, color=theme.TEXT) -> int:
        rendered = font.render(text, True, color)
        surface.blit(rendered, (x, y))
        return y + rendered.get_height() + 2
