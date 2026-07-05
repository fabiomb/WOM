"""Efectos de sonido (SFX): fragor de batalla y marcha de tropas.

A diferencia de `MusicPlayer` (que usa el streaming de `pygame.mixer.music`
para la música de fondo), los efectos suenan por **canales del mixer**
(`pygame.mixer.Sound`), así conviven con la música sin cortarla — el jugador
oye la ambientación de la batalla *sobre* la música. Los archivos viven en
`data/assets/audio/` (assets del juego, no la carpeta de música del usuario).

Hay dos tipos de disparo:
- **ambientación en loop** (marcha / fragor de batalla / marcha en el mapa),
  en un canal reservado, con arranque/parada explícitos e idempotentes; y
- **efectos puntuales** (ataques por clase, muertes), con un *cooldown* por
  evento para no saturar cuando decenas de fichas atacan a la vez.

Resiliente por diseño, igual que la música: si el mixer no inicializa (sin
placa de audio, headless) o falta un archivo, el reproductor queda mudo pero
el juego sigue; el interruptor y el volumen se persisten igual (settings.json).
"""

from __future__ import annotations

import random
from pathlib import Path

import pygame
from pygame import mixer

from wom.paths import resource_root
from wom.persistence.settings import Settings, load_settings, save_settings

# Carpeta de los efectos (junto a los assets, no la de música del usuario).
SFX_FOLDER = "data/assets/audio"
SFX_EXTENSIONS = (".mp3", ".ogg", ".wav")

# Eventos lógicos → archivos candidatos (sin extensión). Se elige uno al azar
# en cada disparo, así los efectos con varias variantes no se vuelven monótonos.
SOUND_EVENTS: dict[str, list[str]] = {
    "march": ["marcha"],                       # marcha de tropas (loop, batalla)
    "battle": ["batalla1", "batalla2"],        # fragor del combate (loop)
    "map_march": ["marcha2"],                  # movimiento en el mapa (loop, recap)
    "attack_soldado": ["soldado1", "soldado2"],
    "attack_partisano": ["soldado1", "soldado2"],
    "attack_arquero": ["arqueros1", "arqueros2"],
    "attack_caballero": ["caballeria"],
    "death": ["muerte1", "muerte2", "muerte3"],
}

# Clase de tropa → evento de ataque (para el efecto del golpe según quién pega).
ATTACK_EVENT: dict[str, str] = {
    "soldado": "attack_soldado",
    "partisano": "attack_partisano",
    "arquero": "attack_arquero",
    "caballero": "attack_caballero",
}


class SoundPlayer:
    """Efectos de sonido sobre canales del mixer, con settings persistidos."""

    NUM_CHANNELS = 16     # amplía los 8 por defecto: muchos efectos simultáneos
    LOOP_CHANNEL = 0      # canal reservado para la ambientación en loop
    AMBIENT_CHANNEL = 1   # canal reservado para el fragor puntual (desvanecible)

    def __init__(self, settings: Settings | None = None, settings_path: Path | None = None):
        self._settings_path = settings_path
        self.settings = settings if settings is not None else load_settings(settings_path)
        self.available = False
        self._cache: dict[str, mixer.Sound | None] = {}
        self._loop_ch: mixer.Channel | None = None
        self._loop_name: str | None = None
        self._ambient_ch: mixer.Channel | None = None
        # Último instante (ticks) en que sonó cada evento puntual, para el cooldown.
        self._last_played: dict[str, int] = {}
        try:
            if not mixer.get_init():
                mixer.init()
            mixer.set_num_channels(self.NUM_CHANNELS)
            # Reserva los canales 0 y 1: los efectos puntuales (Sound.play sin
            # canal) no los pisan, así la ambientación en loop (0) y el fragor
            # desvanecible (1) no se cortan.
            mixer.set_reserved(2)
            self._loop_ch = mixer.Channel(self.LOOP_CHANNEL)
            self._ambient_ch = mixer.Channel(self.AMBIENT_CHANNEL)
            self.available = True
        except pygame.error:
            self.available = False

    # --- carga / selección de sonidos --------------------------------------

    def folder_path(self) -> Path:
        return resource_root() / SFX_FOLDER

    def _sound(self, name: str) -> mixer.Sound | None:
        """Carga (y cachea) un efecto por nombre; None si falta o no carga.

        El None también se cachea para no reintentar el disco en cada disparo."""
        if name in self._cache:
            return self._cache[name]
        snd: mixer.Sound | None = None
        folder = self.folder_path()
        for ext in SFX_EXTENSIONS:
            path = folder / f"{name}{ext}"
            if path.exists():
                try:
                    snd = mixer.Sound(str(path))
                except pygame.error:
                    snd = None
                break
        self._cache[name] = snd
        return snd

    def _pick(self, event: str) -> mixer.Sound | None:
        names = SOUND_EVENTS.get(event)
        if not names:
            return None
        return self._sound(random.choice(names))

    # --- efectos puntuales -------------------------------------------------

    def play(self, event: str) -> None:
        """Reproduce una variante del evento (una vez) en un canal libre."""
        if not self.available or not self.settings.sfx_enabled:
            return
        snd = self._pick(event)
        if snd is None:
            return
        snd.set_volume(self.settings.sfx_volume)
        snd.play()

    def play_throttled(self, event: str, cooldown_ms: int = 350) -> None:
        """Como `play`, pero ignora disparos del mismo evento demasiado seguidos
        (evita el ruido de decenas de golpes/muertes en el mismo frame)."""
        if not self.available or not self.settings.sfx_enabled:
            return
        now = pygame.time.get_ticks()
        if now - self._last_played.get(event, -10**9) < cooldown_ms:
            return
        self._last_played[event] = now
        self.play(event)

    def play_attack(self, class_id: str) -> None:
        """Efecto del golpe según la clase que ataca (arquero/caballero/…)."""
        event = ATTACK_EVENT.get(class_id)
        if event is not None:
            self.play_throttled(event, 300)

    # --- ambientación en loop (marcha / fragor / marcha en el mapa) --------

    def start_loop(self, event: str) -> None:
        """Arranca una ambientación en loop en el canal reservado.

        Idempotente por evento: si esa misma ambientación ya está sonando no la
        reinicia (cambiar de `march` a `battle`, en cambio, sí la reemplaza)."""
        if not self.available or self._loop_ch is None or not self.settings.sfx_enabled:
            return
        if self._loop_name == event and self._loop_ch.get_busy():
            return
        snd = self._pick(event)
        if snd is None:
            return
        snd.set_volume(self.settings.sfx_volume)
        self._loop_ch.play(snd, loops=-1)
        self._loop_name = event

    def stop_loop(self, fade_ms: int = 0) -> None:
        """Corta la ambientación en loop (fin de batalla / de la animación).

        Con `fade_ms` > 0 hace un fundido de salida en vez de cortar de golpe
        (p. ej. la marcha en el mapa: al terminar el movimiento se desvanece en
        ~1 s, así el avance de las tropas no se corta abruptamente)."""
        self._loop_name = None
        if self.available and self._loop_ch is not None:
            if fade_ms > 0:
                self._loop_ch.fadeout(fade_ms)
            else:
                self._loop_ch.stop()

    # --- fragor puntual desvanecible (choque en el recap del mapa) ----------

    def play_ambient(self, event: str) -> None:
        """Reproduce un efecto ambiental **una vez** en un canal propio, para
        poder cortarlo/desvanecerlo con `fade_ambient` (a diferencia de `play`,
        que dispara y olvida). Se usa para el fragor del choque del recap: el
        mp3 dura más que la animación, así que luego se desvanece."""
        if not self.available or self._ambient_ch is None or not self.settings.sfx_enabled:
            return
        snd = self._pick(event)
        if snd is None:
            return
        snd.set_volume(self.settings.sfx_volume)
        self._ambient_ch.play(snd)

    def fade_ambient(self, fade_ms: int = 1500) -> None:
        """Desvanece (o corta si `fade_ms` = 0) el fragor puntual en curso."""
        if not self.available or self._ambient_ch is None:
            return
        if fade_ms > 0:
            self._ambient_ch.fadeout(fade_ms)
        else:
            self._ambient_ch.stop()

    # --- settings (cada cambio se aplica y se guarda en el momento) --------

    def set_enabled(self, enabled: bool) -> None:
        self.settings.sfx_enabled = enabled
        self._save()
        if not enabled:
            self.stop_loop()
            self.fade_ambient(0)

    def set_volume(self, volume: float) -> None:
        self.settings.sfx_volume = round(min(1.0, max(0.0, volume)), 2)
        self._save()
        if self.available and self._loop_ch is not None and self._loop_ch.get_busy():
            self._loop_ch.set_volume(self.settings.sfx_volume)

    def save(self) -> None:
        self._save()

    def _save(self) -> None:
        save_settings(self.settings, self._settings_path)
