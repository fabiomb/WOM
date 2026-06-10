"""Reproductor de música: playlist desde una carpeta de mp3 (pygame.mixer).

La playlist se arma escaneando la carpeta de settings (mp3/ogg). El orden
puede ser aleatorio (se baraja) o secuencial (empieza en un tema al azar y
sigue en orden, como pide el diseño: al iniciar el juego suena un tema al
azar). Cuando un tema termina, pygame postea MUSIC_END_EVENT y el loop de
la app se lo pasa a `handle_event`, que avanza la playlist.

Resiliente por diseño: si el mixer no inicializa (sin placa de audio, modo
headless) o un archivo no carga, el reproductor queda mudo pero el juego y
los menúes siguen funcionando; los cambios de settings se persisten igual.
"""

from __future__ import annotations

import random
from pathlib import Path

import pygame
from pygame import mixer

from wom.persistence.settings import Settings, load_settings, save_settings

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MUSIC_EXTENSIONS = (".mp3", ".ogg")
MUSIC_END_EVENT = pygame.event.custom_type()  # fin del tema en reproducción


class MusicPlayer:
    """Playlist + controles sobre pygame.mixer.music, con settings persistidos."""

    def __init__(self, settings: Settings | None = None, settings_path: Path | None = None):
        self._settings_path = settings_path
        self.settings = settings if settings is not None else load_settings(settings_path)
        self.tracks: list[Path] = []   # playlist (orden de archivo, estable)
        self.order: list[int] = []     # orden de reproducción (índices en tracks)
        self.position = 0              # posición dentro de `order`
        self.playing = False
        self.paused = False
        self.available = False  # False: sin audio, todos los controles son no-op
        try:
            if not mixer.get_init():
                mixer.init()
            mixer.music.set_endevent(MUSIC_END_EVENT)
            self.available = True
        except pygame.error:
            self.available = False
        self.reload_folder()
        if self.available:
            mixer.music.set_volume(self.settings.music_volume)

    # --- playlist ----------------------------------------------------------

    @property
    def current(self) -> Path | None:
        """Archivo del tema actual (o None si la playlist está vacía)."""
        if not self.order:
            return None
        return self.tracks[self.order[self.position]]

    def folder_path(self) -> Path:
        """Carpeta de música resuelta (las relativas cuelgan del proyecto)."""
        folder = Path(self.settings.music_folder)
        return folder if folder.is_absolute() else PROJECT_ROOT / folder

    def reload_folder(self) -> None:
        """Rearma la playlist desde la carpeta de settings."""
        folder = self.folder_path()
        self.tracks = (
            sorted(p for p in folder.iterdir() if p.suffix.lower() in MUSIC_EXTENSIONS)
            if folder.is_dir() else []
        )
        self._rebuild_order()

    def _rebuild_order(self) -> None:
        """Orden de reproducción: barajado, o secuencial desde un tema al azar."""
        self.order = list(range(len(self.tracks)))
        if self.settings.music_shuffle:
            random.shuffle(self.order)
        elif self.order:
            start = random.randrange(len(self.order))
            self.order = self.order[start:] + self.order[:start]
        self.position = 0

    # --- controles ---------------------------------------------------------

    def start(self) -> None:
        """Arranque del juego: reproduce (un tema al azar) si está habilitada."""
        if self.settings.music_enabled:
            self.play()

    def play(self) -> None:
        """(Re)produce el tema actual desde el inicio; saltea los que no carguen."""
        if not self.available or not self.order:
            self.playing = False
            return
        for _ in range(len(self.order)):
            try:
                mixer.music.load(str(self.current))
                mixer.music.play()
                self.playing, self.paused = True, False
                return
            except pygame.error:
                self.position = (self.position + 1) % len(self.order)
        self.playing = False  # ningún archivo de la carpeta se pudo cargar

    def toggle_pause(self) -> None:
        if not self.available:
            return
        if not self.playing:
            self.play()
        elif self.paused:
            mixer.music.unpause()
            self.paused = False
        else:
            mixer.music.pause()
            self.paused = True

    def stop(self) -> None:
        self.playing = False
        self.paused = False
        if self.available:
            mixer.music.stop()

    def next(self) -> None:
        self._jump(+1)

    def prev(self) -> None:
        self._jump(-1)

    def _jump(self, delta: int) -> None:
        if not self.order:
            return
        self.position = (self.position + delta) % len(self.order)
        if self.settings.music_enabled:
            self.play()

    def handle_event(self, event: pygame.event.Event) -> bool:
        """Procesa el fin del tema actual; devuelve True si consumió el evento.

        `get_busy()` filtra los eventos de fin viejos (stop/cambio manual ya
        cargó otro tema cuando el evento llega).
        """
        if event.type != MUSIC_END_EVENT:
            return False
        if self.playing and not self.paused and not mixer.music.get_busy():
            self.position = (self.position + 1) % len(self.order or [0])
            self.play()
        return True

    # --- settings (cada cambio se aplica y se guarda en el momento) --------

    def set_enabled(self, enabled: bool) -> None:
        self.settings.music_enabled = enabled
        self._save()
        if enabled:
            self.play()
        else:
            self.stop()

    def set_volume(self, volume: float) -> None:
        self.settings.music_volume = round(min(1.0, max(0.0, volume)), 2)
        self._save()
        if self.available:
            mixer.music.set_volume(self.settings.music_volume)

    def set_folder(self, folder: str) -> None:
        self.settings.music_folder = folder
        self._save()
        was_playing = self.playing
        self.stop()
        self.reload_folder()
        if was_playing and self.settings.music_enabled:
            self.play()

    def set_shuffle(self, shuffle: bool) -> None:
        self.settings.music_shuffle = shuffle
        self._save()
        current = self.current
        self._rebuild_order()
        # el tema actual conserva su lugar: sigue sonando y el nuevo orden
        # aplica a partir del próximo salto
        if current is not None:
            index = self.tracks.index(current)
            self.order.remove(index)
            self.order.insert(self.position, index)

    def save(self) -> None:
        """Persiste los settings (también los ajenos a la música, ej. video)."""
        self._save()

    def _save(self) -> None:
        save_settings(self.settings, self._settings_path)
