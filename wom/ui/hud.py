"""Panel lateral: estado de la partida, ejército seleccionado, fin de turno."""

from __future__ import annotations

import pygame

from wom.core.army import Army
from wom.core.game import Game
from wom.core.victory import VictoryResult, VictoryMode
from wom.core.worldmap import Fort
from wom.ui import scale, theme
from wom.ui import assets


MAX_ARMY_ROWS = 8  # filas de la lista de ejércitos propios


class Hud:
    def __init__(self, rect: pygame.Rect, human_id: int = 0):
        self.rect = rect
        self.human_id = human_id
        self.title_font = pygame.font.SysFont(None, 34)
        self.result_font = pygame.font.SysFont(None, 64)
        self.font = pygame.font.SysFont(None, 22)
        self.small_font = pygame.font.SysFont(None, 18)
        # Ilustraciones de fin de partida (victoria/derrota), cargadas a
        # demanda; None si el asset falta.
        self._result_images: dict[str, pygame.Surface | None] = {}
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

        if result.winner is None:
            title_text, color, image_name = "Empate", theme.TEXT, None
        elif result.winner == self.human_id:
            title_text, color, image_name = "¡Victoria!", (235, 205, 90), "victory"
        else:
            title_text, color, image_name = "Derrota", (210, 80, 80), "defeat"

        image = self._result_image(image_name) if image_name else None

        cx = surface.get_rect().centerx
        # Líneas de estadística que se muestran bajo la imagen.
        human = game.players[self.human_id]
        stats: list[str] = [
            self._result_reason(result),
            "",
            f"Turnos jugados: {game.turn}",
            f"Batallas libradas: {game.battles_fought}",
            f"Tus bajas: {human.troops_lost}",
        ]
        for player in game.players:
            if player.id != self.human_id:
                stats.append(f"Bajas de {player.name}: {player.troops_lost}")
        total_losses = sum(p.troops_lost for p in game.players)
        stats.append(f"Bajas totales: {total_losses}")

        # Alto total del bloque para centrarlo verticalmente.
        title = self.result_font.render(title_text, True, color)
        line_h = self.font.get_height() + 6
        block_h = title.get_height() + 18
        if image is not None:
            block_h += image.get_height() + 18
        block_h += len(stats) * line_h + 12 + self.small_font.get_height()

        y = surface.get_rect().centery - block_h // 2
        surface.blit(title, title.get_rect(midtop=(cx, y)))
        y += title.get_height() + 18

        if image is not None:
            surface.blit(image, image.get_rect(midtop=(cx, y)))
            y += image.get_height() + 18

        for line in stats:
            if not line:
                y += line_h // 2
                continue
            rendered = self.font.render(line, True, theme.TEXT)
            surface.blit(rendered, rendered.get_rect(midtop=(cx, y)))
            y += line_h

        y += 12
        hint = self.small_font.render("ESC para volver al menú", True, theme.TEXT_DIM)
        surface.blit(hint, hint.get_rect(midtop=(cx, y)))

    def _result_reason(self, result: VictoryResult) -> str:
        """Motivo del fin de partida redactado desde la perspectiva del
        jugador humano. `result.reason` viene siempre en clave del ganador,
        así que en derrota se reformula para referirse al propio jugador."""
        if result.winner is None:
            return "Aniquilación mutua"
        won = result.winner == self.human_id
        if result.mode is VictoryMode.TOTAL:
            return (
                "El rival se quedó sin ejércitos ni fuertes"
                if won else "Te has quedado sin ejércitos ni fuertes"
            )
        if result.mode is VictoryMode.FLAGS:
            return (
                "Controlas todas las banderas"
                if won else "El rival controla todas las banderas"
            )
        if result.mode is VictoryMode.TIME:
            return (
                "Lograste superioridad de territorio y tropas al turno límite"
                if won else "El rival logró superioridad de territorio y tropas al turno límite"
            )
        return result.reason

    def _result_image(self, name: str) -> pygame.Surface | None:
        """Carga (y cachea) la ilustración de victoria/derrota, escalada para
        caber en la pantalla. Devuelve None si el asset no existe."""
        if name not in self._result_images:
            self._result_images[name] = assets.load_image(name)
        image = self._result_images[name]
        if image is None:
            return None
        max_w = min(420, self.rect.left - 40 if self.rect.left > 80 else 420)
        max_h = 320
        w, h = image.get_size()
        factor = min(max_w / w, max_h / h, 1.0)
        if factor < 1.0:
            image = pygame.transform.smoothscale(
                image, (max(1, int(w * factor)), max(1, int(h * factor)))
            )
            self._result_images[name] = image
        return image

    def _text(self, surface, text, x, y, font, color=theme.TEXT) -> int:
        rendered = font.render(text, True, color)
        surface.blit(rendered, (x, y))
        return y + rendered.get_height() + 2
