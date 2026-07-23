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
    def __init__(self, rect: pygame.Rect, human_id: int = 0, net_mode: bool = False):
        self.rect = rect
        self.human_id = human_id
        # En red el sidebar suma chat e indicadores y no muestra "Guardar", así
        # que los botones se reacomodan dejando lugar al panel de red abajo.
        self.net_mode = net_mode
        self.title_font = pygame.font.SysFont(None, 34)
        self.font = pygame.font.SysFont(None, 22)
        self.small_font = pygame.font.SysFont(None, 18)
        # Fin de partida: tipografías más grandes para que el resultado se lea
        # claro (título grande + resumen a buen tamaño).
        self.result_title_font = pygame.font.SysFont(None, 88)
        self.result_stat_font = pygame.font.SysFont(None, 32)
        self.result_hint_font = pygame.font.SysFont(None, 24)
        # Ilustraciones de fin de partida (victoria/derrota), cargadas a
        # demanda; None si el asset falta.
        self._result_images: dict[str, pygame.Surface | None] = {}
        # Video de fin de partida (victory.mp4/defeat.mp4): se reproduce ~5s y
        # funde a la imagen. None hasta que la pantalla de fin lo dispara.
        self._result_video: object | None = None
        self._result_video_name: str | None = None
        x, w, bottom = rect.x + 20, rect.width - 40, rect.bottom
        self.button = pygame.Rect(x, bottom - 60, w, 42)  # fin del turno
        if net_mode:
            self.save_button = pygame.Rect(x, bottom - 110, w, 36)  # sin uso en red
            self.chat_input = pygame.Rect(x, bottom - 104, w, 32)
            create_y = bottom - 230  # arriba del bloque de chat
        else:
            self.save_button = pygame.Rect(x, bottom - 110, w, 36)
            self.chat_input = pygame.Rect(x, bottom - 104, w, 32)
            create_y = bottom - 160
        self.create_button = pygame.Rect(x, create_y, w, 42)
        self._create_button_visible = False
        # Mismo lugar que "Crear ejército": nunca se ven a la vez (fuerte
        # seleccionado vs ejército seleccionado).
        self.split_button = pygame.Rect(x, create_y, w, 42)
        self._split_button_visible = False

    def hit_end_turn(self, point: tuple[int, int]) -> bool:
        return self.button.collidepoint(point)

    def hit_save(self, point: tuple[int, int]) -> bool:
        return self.save_button.collidepoint(point)

    def hit_create_army(self, point: tuple[int, int]) -> bool:
        return self._create_button_visible and self.create_button.collidepoint(point)

    def hit_split(self, point: tuple[int, int]) -> bool:
        return self._split_button_visible and self.split_button.collidepoint(point)

    def hit_chat_input(self, point: tuple[int, int]) -> bool:
        return self.net_mode and self.chat_input.collidepoint(point)

    def draw(
        self,
        surface: pygame.Surface,
        game: Game,
        selected: Army | None,
        selected_fort: Fort | None = None,
        creation_pending: bool = False,
        result: VictoryResult | None = None,
        notice: str | None = None,
        net_panel: dict | None = None,
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
            y = self._text(
                surface, "F1: cómo jugar (ayuda)",
                x, y, self.small_font, theme.SELECTION,
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
            if net_panel is not None:
                self._draw_net_panel(surface, net_panel)
            if notice:
                rendered = self.small_font.render(notice, True, theme.SELECTION)
                anchor = (
                    self.create_button.top - 6 if net_panel is not None
                    else self.save_button.top - 8
                )
                surface.blit(
                    rendered,
                    rendered.get_rect(midbottom=(self.rect.centerx, anchor)),
                )
            if net_panel is None:  # en red no se guarda
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

    def _draw_net_panel(self, surface: pygame.Surface, panel: dict) -> None:
        """Indicadores de red (estado/timer), log de chat y caja de entrada.

        Se dibuja anclado abajo del sidebar, sobre el botón de fin de turno.
        """
        x = self.rect.x + 20
        box = self.chat_input
        max_w = box.width - 16
        line_h = self.small_font.get_height() + 2

        # Log de chat: las últimas líneas, justo encima de la caja de entrada.
        log = panel.get("chat_log", [])[-5:]
        ly = box.top - 6 - len(log) * line_h
        for name, text in log:
            line = self._fit(f"{name}: {text}", self.small_font, max_w)
            surface.blit(self.small_font.render(line, True, theme.TEXT), (x, ly))
            ly += line_h

        # Estado de conexión y reloj de turno, encima del log.
        sy = box.top - 6 - len(log) * line_h - 2 * line_h - 6
        if panel.get("disconnected"):
            status, color = "Rival desconectado", (210, 80, 80)
        elif panel.get("llm_status"):
            # Rival LLM: "X está pensando… Ns" — feedback de que el modelo
            # sigue generando la movida, no que se cayó la conexión.
            status, color = panel["llm_status"], (235, 205, 90)
        elif panel.get("waiting"):
            status, color = "Esperando al rival…", (235, 205, 90)
        else:
            status = f"En partida con {panel.get('peer_name') or 'rival'}"
            color = (120, 200, 120)
        surface.blit(
            self.small_font.render(self._fit(status, self.small_font, max_w), True, color),
            (x, sy),
        )
        seconds = panel.get("seconds_left")
        if seconds is not None:
            timer = f"Tiempo de turno: {seconds}s"
            tcolor = (210, 80, 80) if seconds <= 5 else theme.TEXT_DIM
            surface.blit(self.small_font.render(timer, True, tcolor), (x, sy + line_h))

        # Caja de entrada de chat.
        active = panel.get("chat_active")
        pygame.draw.rect(surface, (30, 34, 40), box, border_radius=6)
        border = theme.SELECTION if active else (90, 96, 104)
        pygame.draw.rect(surface, border, box, 2, border_radius=6)
        if active:
            shown, col = panel.get("chat_buffer", "") + "_", theme.TEXT
        elif panel.get("chat_buffer"):
            shown, col = panel["chat_buffer"], theme.TEXT
        else:
            shown, col = "T para chatear…", theme.TEXT_DIM
        surface.blit(
            self.small_font.render(self._fit(shown, self.small_font, max_w), True, col),
            (box.x + 8, box.y + 7),
        )

    def _fit(self, text: str, font: pygame.font.Font, max_w: int) -> str:
        """Recorta el texto con elipsis para que entre en `max_w` píxeles."""
        if font.size(text)[0] <= max_w:
            return text
        while text and font.size(text + "…")[0] > max_w:
            text = text[:-1]
        return text + "…"

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

    # Medidas del panel "pergamino" de fin de partida.
    _RESULT_PAD = 36          # margen interno de la caja
    _RESULT_GAP = 36          # separación entre la columna media y el texto
    _RESULT_MEDIA = 440       # lado de la ilustración/video (cuadrado)
    _RESULT_TEXT_W = 600      # ancho de la columna de resumen

    def _draw_game_over(
        self, surface: pygame.Surface, game: Game, result: VictoryResult
    ) -> None:
        overlay = pygame.Surface(surface.get_size(), pygame.SRCALPHA)
        overlay.fill(theme.GAMEOVER_BG)
        surface.blit(overlay, (0, 0))

        if result.winner is None:
            title_text, color, image_name = "Empate", theme.PARCHMENT_INK, None
        elif result.winner == self.human_id:
            title_text, color, image_name = "¡Victoria!", theme.VICTORY_INK, "victory"
        else:
            title_text, color, image_name = "Derrota", theme.DEFEAT_INK, "defeat"

        media_side = self._RESULT_MEDIA if image_name else 0
        image = (
            self._result_image(image_name, media_side) if image_name else None
        )
        if image is None:
            media_side = 0  # sin ilustración: solo la columna de texto

        # --- contenido de la columna de resumen (texto) -------------------
        human = game.players[self.human_id]
        title = self.result_title_font.render(title_text, True, color)
        reason_lines = self._wrap(
            self._result_reason(result), self.result_stat_font, self._RESULT_TEXT_W
        )
        stats: list[str] = [
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

        stat_h = self.result_stat_font.get_height() + 8
        rule_gap = 22
        text_h = title.get_height() + 14 + rule_gap
        text_h += len(reason_lines) * stat_h + 8
        text_h += sum(stat_h if line else stat_h // 2 for line in stats)
        text_h += 22 + self.result_hint_font.get_height()

        pad, gap = self._RESULT_PAD, self._RESULT_GAP
        content_h = max(media_side, text_h)
        box_w = pad * 2 + media_side + (gap if media_side else 0) + self._RESULT_TEXT_W
        box_h = pad * 2 + content_h
        box = pygame.Rect(0, 0, box_w, box_h)
        box.center = surface.get_rect().center

        # --- panel de pergamino con borde de tinta ------------------------
        pygame.draw.rect(surface, theme.PARCHMENT_BG, box, border_radius=14)
        lower = pygame.Rect(box.x, box.centery, box.w, box.h // 2)
        pygame.draw.rect(
            surface, theme.PARCHMENT_BG_DARK, lower,
            border_bottom_left_radius=14, border_bottom_right_radius=14,
        )
        pygame.draw.rect(surface, theme.PARCHMENT_BORDER, box, 3, border_radius=14)
        pygame.draw.rect(
            surface, theme.PARCHMENT_BORDER, box.inflate(-12, -12), 1, border_radius=10
        )

        # --- columna media: ilustración + (si aplica) video por encima ----
        if image is not None:
            media = pygame.Rect(0, 0, media_side, media_side)
            media.topleft = (box.x + pad, box.centery - media_side // 2)
            surface.blit(image, image.get_rect(center=media.center))
            self._draw_result_video(surface, media, image_name)
            pygame.draw.rect(
                surface, theme.PARCHMENT_BORDER, media.inflate(8, 8), 3, border_radius=6
            )

        # --- columna de resumen -------------------------------------------
        tx = box.x + pad + (media_side + gap if media_side else 0)
        ty = box.centery - text_h // 2
        surface.blit(title, (tx, ty))
        ty += title.get_height() + 14
        pygame.draw.line(
            surface, theme.PARCHMENT_BORDER, (tx, ty), (tx + self._RESULT_TEXT_W, ty), 2
        )
        ty += rule_gap

        for line in reason_lines:
            rendered = self.result_stat_font.render(line, True, theme.PARCHMENT_INK)
            surface.blit(rendered, (tx, ty))
            ty += stat_h
        ty += 8
        for line in stats:
            if not line:
                ty += stat_h // 2
                continue
            rendered = self.result_stat_font.render(line, True, theme.PARCHMENT_INK_DIM)
            surface.blit(rendered, (tx, ty))
            ty += stat_h

        ty += 22
        hint = self.result_hint_font.render(
            "ESC para volver al menú", True, theme.PARCHMENT_INK_DIM
        )
        surface.blit(hint, (tx, ty))

    @staticmethod
    def _wrap(text: str, font: pygame.font.Font, max_w: int) -> list[str]:
        """Parte `text` en líneas que entren en `max_w` (corte por palabras)."""
        words = text.split()
        lines: list[str] = []
        current = ""
        for word in words:
            probe = f"{current} {word}".strip()
            if font.size(probe)[0] <= max_w or not current:
                current = probe
            else:
                lines.append(current)
                current = word
        if current:
            lines.append(current)
        return lines or [""]

    def _draw_result_video(
        self, surface: pygame.Surface, rect: pygame.Rect, name: str
    ) -> None:
        """Reproduce el clip de fin de partida sobre la ilustración (que queda
        de base): arranca el video la primera vez y lo va dibujando. Degrada a
        la imagen fija si no se puede reproducir (headless, sin ffmpeg)."""
        from wom.ui.videoclip import VideoClip, can_play_video

        if not can_play_video():
            return
        if self._result_video_name != name:
            self.close_result_video()
            mp4 = assets.ASSETS_DIR / f"{name}.mp4"
            self._result_video = VideoClip(mp4, (rect.width, rect.height))
            self._result_video_name = name
        clip = self._result_video
        if clip is not None and getattr(clip, "available", False):
            clip.draw(surface, rect)

    def result_video_audible(self) -> bool:
        """True si el clip de fin de partida está sonando ahora mismo (para que
        GameScreen agache la música mientras dure)."""
        clip = self._result_video
        return clip is not None and getattr(clip, "audible", False)

    def close_result_video(self) -> None:
        """Libera el clip de fin de partida (corta ffmpeg/audio). La llama
        GameScreen al salir al menú."""
        if self._result_video is not None:
            close = getattr(self._result_video, "close", None)
            if callable(close):
                close()
        self._result_video = None
        self._result_video_name = None

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

    def _result_image(self, name: str, side: int) -> pygame.Surface | None:
        """Carga (y cachea) la ilustración de victoria/derrota escalada a un
        cuadrado de `side`×`side` (mismo encuadre que el video). None si falta.

        Se cachea por `(name, side)` para no reescalar cada frame."""
        key = f"{name}@{side}"
        if key not in self._result_images:
            original = assets.load_image(name)
            self._result_images[key] = (
                pygame.transform.smoothscale(original, (side, side))
                if original is not None else None
            )
        return self._result_images[key]

    def _text(self, surface, text, x, y, font, color=theme.TEXT) -> int:
        rendered = font.render(text, True, color)
        surface.blit(rendered, (x, y))
        return y + rendered.get_height() + 2
