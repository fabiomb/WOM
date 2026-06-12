"""Panel lateral: estado de la partida, ejército seleccionado, fin de turno."""

from __future__ import annotations

import pygame

from wom.core.army import Army
from wom.core.game import Game
from wom.core.victory import VictoryResult
from wom.core.worldmap import Fort
from wom.ui import scale, theme


MAX_ARMY_ROWS = 8  # filas de la lista de ejércitos propios


class Hud:
    def __init__(self, rect: pygame.Rect, human_id: int = 0):
        self.rect = rect
        self.human_id = human_id
        self.title_font = pygame.font.SysFont(None, 34)
        self.font = pygame.font.SysFont(None, 22)
        self.small_font = pygame.font.SysFont(None, 18)
        self.button = pygame.Rect(
            rect.x + 20, rect.bottom - 60, rect.width - 40, 42
        )
        self.save_button = pygame.Rect(
            rect.x + 20, rect.bottom - 110, rect.width - 40, 36
        )
        self.create_button = pygame.Rect(
            rect.x + 20, rect.bottom - 160, rect.width - 40, 42
        )
        self._create_button_visible = False
        # Mismo lugar que "Crear ejército": nunca se ven a la vez (fuerte
        # seleccionado vs ejército seleccionado).
        self.split_button = pygame.Rect(
            rect.x + 20, rect.bottom - 160, rect.width - 40, 42
        )
        self._split_button_visible = False

    def hit_end_turn(self, point: tuple[int, int]) -> bool:
        return self.button.collidepoint(point)

    def hit_save(self, point: tuple[int, int]) -> bool:
        return self.save_button.collidepoint(point)

    def hit_create_army(self, point: tuple[int, int]) -> bool:
        return self._create_button_visible and self.create_button.collidepoint(point)

    def hit_split(self, point: tuple[int, int]) -> bool:
        return self._split_button_visible and self.split_button.collidepoint(point)

    def draw(
        self,
        surface: pygame.Surface,
        game: Game,
        selected: Army | None,
        selected_fort: Fort | None = None,
        creation_pending: bool = False,
        result: VictoryResult | None = None,
        notice: str | None = None,
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
                surface,
                f"  {len(armies)} ejércitos · {troops} tropas · {player.troops_lost} bajas",
                x, y, self.small_font, theme.TEXT_DIM,
            )
            y = self._text(
                surface,
                f"  {forts} fuertes · {towns} pueblos · comida {player.food}",
                x, y, self.small_font, theme.TEXT_DIM,
            )
            y += 6

        y = self._draw_army_list(surface, game, selected, x, y)

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
                surface, "Click en el ejército o ESC: confirmar ruta", x, y,
                self.small_font, theme.TEXT_DIM,
            )
            y = self._text(
                surface, "Click derecho: borrar/deseleccionar", x, y,
                self.small_font, theme.TEXT_DIM,
            )
            y = self._text(
                surface, "Doble click: fijar la ruta y soltar el ejército", x, y,
                self.small_font, theme.TEXT_DIM,
            )
            y = self._text(
                surface, "Shift+click en otro propio aledaño: fusionar", x, y,
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
            self._split_button_visible = False
            self._draw_game_over(surface, game, result)
        else:
            self._create_button_visible = (
                selected_fort is not None and selected_fort.reserve_total > 0
            )
            if self._create_button_visible:
                over = self.create_button.collidepoint(scale.mouse_pos())
                pygame.draw.rect(
                    surface, theme.BUTTON_BG_OVER if over else theme.BUTTON_BG,
                    self.create_button, border_radius=6,
                )
                text = "Cancelar creación" if creation_pending else "Crear ejército"
                label = self.font.render(text, True, theme.TEXT)
                surface.blit(label, label.get_rect(center=self.create_button.center))
            self._split_button_visible = (
                selected is not None and selected.total_troops >= 2
            )
            if self._split_button_visible:
                over = self.split_button.collidepoint(scale.mouse_pos())
                pygame.draw.rect(
                    surface, theme.BUTTON_BG_OVER if over else theme.BUTTON_BG,
                    self.split_button, border_radius=6,
                )
                label = self.font.render("Dividir ejército (D)", True, theme.TEXT)
                surface.blit(label, label.get_rect(center=self.split_button.center))
            if notice:
                rendered = self.small_font.render(notice, True, theme.SELECTION)
                surface.blit(
                    rendered,
                    rendered.get_rect(midbottom=(self.rect.centerx, self.save_button.top - 8)),
                )
            over = self.save_button.collidepoint(scale.mouse_pos())
            pygame.draw.rect(
                surface, (70, 78, 86) if over else (50, 56, 62),
                self.save_button, border_radius=6,
            )
            label = self.small_font.render("Guardar partida (G)", True, theme.TEXT)
            surface.blit(label, label.get_rect(center=self.save_button.center))
            over = self.button.collidepoint(scale.mouse_pos())
            pygame.draw.rect(
                surface, theme.BUTTON_BG_OVER if over else theme.BUTTON_BG,
                self.button, border_radius=6,
            )
            label = self.font.render("Fin del turno (Enter)", True, theme.TEXT)
            surface.blit(label, label.get_rect(center=self.button.center))

    def _draw_army_list(
        self, surface: pygame.Surface, game: Game, selected: Army | None, x: int, y: int
    ) -> int:
        """Lista de los ejércitos del jugador humano con sus tropas."""
        armies = game.armies_of(self.human_id)
        if not armies:
            return y
        y += 4
        y = self._text(surface, "Tus ejércitos", x, y, self.font)
        for army in armies[:MAX_ARMY_ROWS]:
            is_selected = selected is not None and army.id == selected.id
            color = theme.SELECTION if is_selected else theme.TEXT_DIM
            ax, ay = army.position
            y = self._text(
                surface,
                f"  #{army.id} · {army.total_troops} tropas · ({ax},{ay})",
                x, y, self.small_font, color,
            )
        if len(armies) > MAX_ARMY_ROWS:
            y = self._text(
                surface, f"  … y {len(armies) - MAX_ARMY_ROWS} más",
                x, y, self.small_font, theme.TEXT_DIM,
            )
        return y + 4

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
        hint = self.small_font.render("ESC para volver al menú", True, theme.TEXT_DIM)
        surface.blit(title, title.get_rect(center=(center[0], center[1] - 30)))
        surface.blit(reason, reason.get_rect(center=center))
        surface.blit(hint, hint.get_rect(center=(center[0], center[1] + 30)))

    def _text(self, surface, text, x, y, font, color=theme.TEXT) -> int:
        rendered = font.render(text, True, color)
        surface.blit(rendered, (x, y))
        return y + rendered.get_height() + 2
