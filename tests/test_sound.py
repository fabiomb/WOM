"""Tests del reproductor de efectos de sonido (SFX) con un mixer falso.

Verifica el mapeo de eventos → archivos, el cooldown de los efectos puntuales,
la ambientación en loop (idempotencia + reemplazo), los settings persistidos y
la resiliencia sin audio. No necesita placa de audio: el mixer es un doble.
"""

import os

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

from types import SimpleNamespace

import pygame
import pytest

import wom.ui.sound as sound_module
from wom.persistence.settings import Settings, load_settings
from wom.ui.sound import SOUND_EVENTS, SoundPlayer


class _FakeSound:
    def __init__(self, path):
        self.path = path
        self.volume = None
        self.plays = 0

    def set_volume(self, v):
        self.volume = v

    def play(self, loops=0):
        self.plays += 1


class _FakeChannel:
    def __init__(self, idx):
        self.idx = idx
        self.busy = False
        self.played: list[tuple] = []
        self.volume = None

        self.faded_ms: int | None = None

    def play(self, snd, loops=0):
        self.busy = True
        self.played.append((snd, loops))

    def stop(self):
        self.busy = False

    def fadeout(self, ms):
        self.faded_ms = ms
        self.busy = False

    def get_busy(self):
        return self.busy

    def set_volume(self, v):
        self.volume = v


def _make_mixer(*, fail=False):
    channels: dict[int, _FakeChannel] = {}
    sounds: list[_FakeSound] = []

    def _sound(path):
        s = _FakeSound(path)
        sounds.append(s)
        return s

    def _channel(idx):
        return channels.setdefault(idx, _FakeChannel(idx))

    def _init():
        if fail:
            raise pygame.error("sin audio")

    fake = SimpleNamespace(
        get_init=lambda: False,
        init=_init,
        set_num_channels=lambda n: None,
        set_reserved=lambda n: None,
        Sound=_sound,
        Channel=_channel,
    )
    return fake, channels, sounds


@pytest.fixture()
def fake_mixer(monkeypatch):
    fake, channels, sounds = _make_mixer()
    monkeypatch.setattr(sound_module, "mixer", fake)
    return SimpleNamespace(mixer=fake, channels=channels, sounds=sounds)


def _player(tmp_path, monkeypatch, **settings_kwargs) -> SoundPlayer:
    """SoundPlayer con una carpeta de audio de prueba (un archivo por variante)."""
    folder = tmp_path / "audio"
    folder.mkdir(exist_ok=True)
    for names in SOUND_EVENTS.values():
        for name in names:
            (folder / f"{name}.mp3").write_bytes(b"")
    monkeypatch.setattr(SoundPlayer, "folder_path", lambda self: folder)
    settings = Settings(**settings_kwargs)
    return SoundPlayer(settings=settings, settings_path=tmp_path / "settings.json")


def test_play_reproduce_una_variante(tmp_path, monkeypatch, fake_mixer):
    player = _player(tmp_path, monkeypatch)
    player.play("death")
    assert len(fake_mixer.sounds) == 1
    snd = fake_mixer.sounds[0]
    assert snd.plays == 1
    assert snd.volume == pytest.approx(0.7)  # volumen de settings por defecto


def test_deshabilitado_no_suena(tmp_path, monkeypatch, fake_mixer):
    player = _player(tmp_path, monkeypatch, sfx_enabled=False)
    player.play("death")
    player.start_loop("battle")
    player.play_ambient("battle")
    assert fake_mixer.sounds == []


def test_play_ambient_se_desvanece(tmp_path, monkeypatch, fake_mixer):
    player = _player(tmp_path, monkeypatch)
    ch = fake_mixer.mixer.Channel(SoundPlayer.AMBIENT_CHANNEL)
    player.play_ambient("battle")
    assert len(ch.played) == 1 and ch.played[0][1] == 0  # una sola vez (no loop)
    player.fade_ambient(1500)                            # se desvanece a los segundos
    assert ch.faded_ms == 1500
    player.play_ambient("battle")
    player.fade_ambient(0)                               # 0 = corte inmediato
    assert not ch.get_busy()


def test_cooldown_agrupa_disparos(tmp_path, monkeypatch, fake_mixer):
    player = _player(tmp_path, monkeypatch)
    t = {"now": 1000}
    monkeypatch.setattr(pygame.time, "get_ticks", lambda: t["now"])

    def total_plays() -> int:
        # Cuenta llamadas a play() (no objetos creados: `death` tiene 3 variantes
        # y el cacheo por nombre puede devolver el mismo Sound en dos disparos).
        return sum(s.plays for s in fake_mixer.sounds)

    player.play_throttled("death", 250)
    player.play_throttled("death", 250)  # dentro del cooldown: se ignora
    assert total_plays() == 1
    t["now"] = 1300  # pasado el cooldown
    player.play_throttled("death", 250)
    assert total_plays() == 2


def test_play_attack_mapea_clase(tmp_path, monkeypatch, fake_mixer):
    player = _player(tmp_path, monkeypatch)
    player.play_attack("caballero")
    assert len(fake_mixer.sounds) == 1
    assert "caballeria" in fake_mixer.sounds[0].path
    player.play_attack("desconocida")  # clase sin efecto: no rompe ni suena
    assert len(fake_mixer.sounds) == 1


def test_loop_idempotente_y_reemplazo(tmp_path, monkeypatch, fake_mixer):
    player = _player(tmp_path, monkeypatch)
    ch = fake_mixer.mixer.Channel(SoundPlayer.LOOP_CHANNEL)
    player.start_loop("march")
    assert len(ch.played) == 1 and ch.played[0][1] == -1  # loops=-1
    player.start_loop("march")  # ya suena esa ambientación: no reinicia
    assert len(ch.played) == 1
    player.start_loop("battle")  # cambia de ambientación: reemplaza
    assert len(ch.played) == 2
    player.stop_loop()
    assert not ch.get_busy()


def test_stop_loop_con_fade(tmp_path, monkeypatch, fake_mixer):
    player = _player(tmp_path, monkeypatch)
    ch = fake_mixer.mixer.Channel(SoundPlayer.LOOP_CHANNEL)
    player.start_loop("map_march")
    player.stop_loop(fade_ms=1000)   # fundido de salida en vez de corte seco
    assert ch.faded_ms == 1000
    assert player._loop_name is None  # queda libre para la próxima ambientación


def test_set_enabled_corta_el_loop_y_persiste(tmp_path, monkeypatch, fake_mixer):
    path = tmp_path / "settings.json"
    player = _player(tmp_path, monkeypatch)
    player.start_loop("battle")
    player.set_enabled(False)
    ch = fake_mixer.mixer.Channel(SoundPlayer.LOOP_CHANNEL)
    assert not ch.get_busy()
    assert load_settings(path).sfx_enabled is False


def test_set_volume_clampea_y_persiste(tmp_path, monkeypatch, fake_mixer):
    path = tmp_path / "settings.json"
    player = _player(tmp_path, monkeypatch)
    player.set_volume(1.5)
    assert player.settings.sfx_volume == 1.0
    assert load_settings(path).sfx_volume == 1.0
    player.set_volume(-0.3)
    assert player.settings.sfx_volume == 0.0


def test_sin_audio_todo_es_noop(tmp_path, monkeypatch):
    fake, _channels, sounds = _make_mixer(fail=True)
    monkeypatch.setattr(sound_module, "mixer", fake)
    folder = tmp_path / "audio"
    folder.mkdir()
    monkeypatch.setattr(SoundPlayer, "folder_path", lambda self: folder)
    player = SoundPlayer(settings=Settings(), settings_path=tmp_path / "s.json")
    assert player.available is False
    player.play("death")
    player.start_loop("battle")
    player.stop_loop()  # no revienta sin canal reservado
    assert sounds == []


def test_archivo_faltante_no_rompe(tmp_path, monkeypatch, fake_mixer):
    folder = tmp_path / "audio"
    folder.mkdir()
    monkeypatch.setattr(SoundPlayer, "folder_path", lambda self: folder)
    player = SoundPlayer(settings=Settings(), settings_path=tmp_path / "s.json")
    player.play("death")  # el archivo no existe: no suena, no rompe
    assert fake_mixer.sounds == []
