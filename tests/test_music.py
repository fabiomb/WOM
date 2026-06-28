"""Tests del reproductor de música (con mixer falso) y de settings.json."""

import os

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

from types import SimpleNamespace

import pygame
import pytest

import wom.ui.music as music_module
from wom.persistence.settings import Settings, load_settings, save_settings
from wom.ui.music import MUSIC_END_EVENT, MusicPlayer


class _FakeMusicChannel:
    """Imita pygame.mixer.music registrando las llamadas."""

    def __init__(self):
        self.calls: list[tuple] = []
        self.loaded: str | None = None
        self.busy = False
        self.volume = 1.0

    def load(self, path):
        self.loaded = path
        self.calls.append(("load", path))

    def play(self):
        self.busy = True
        self.calls.append(("play",))

    def pause(self):
        self.calls.append(("pause",))

    def unpause(self):
        self.calls.append(("unpause",))

    def stop(self):
        self.busy = False
        self.calls.append(("stop",))

    def set_volume(self, volume):
        self.volume = volume

    def set_endevent(self, event_type):
        pass

    def get_busy(self):
        return self.busy


@pytest.fixture()
def fake_mixer(monkeypatch):
    fake = SimpleNamespace(
        music=_FakeMusicChannel(), get_init=lambda: True, init=lambda: None
    )
    monkeypatch.setattr(music_module, "mixer", fake)
    return fake


def _player(tmp_path, n_tracks: int = 3, **settings_kwargs) -> MusicPlayer:
    folder = tmp_path / "music"
    folder.mkdir(exist_ok=True)
    for i in range(n_tracks):
        (folder / f"track{i}.mp3").write_bytes(b"")
    settings = Settings(**({"music_folder": str(folder)} | settings_kwargs))
    return MusicPlayer(settings=settings, settings_path=tmp_path / "settings.json")


def test_playlist_desde_carpeta(tmp_path, fake_mixer):
    player = _player(tmp_path)
    assert [t.name for t in player.tracks] == ["track0.mp3", "track1.mp3", "track2.mp3"]
    assert sorted(player.order) == [0, 1, 2]


def test_carpeta_inexistente_no_rompe(tmp_path, fake_mixer):
    player = MusicPlayer(
        settings=Settings(music_folder=str(tmp_path / "no_existe")),
        settings_path=tmp_path / "s.json",
    )
    assert player.tracks == []
    player.start()
    player.next()
    player.toggle_pause()
    player.stop()  # todos no-op, sin excepciones
    assert not player.playing


def test_play_pausa_stop(tmp_path, fake_mixer):
    player = _player(tmp_path)
    player.start()
    assert player.playing and not player.paused
    assert fake_mixer.music.loaded == str(player.current)
    player.toggle_pause()
    assert player.paused
    player.toggle_pause()
    assert player.playing and not player.paused
    player.stop()
    assert not player.playing


def test_siguiente_y_anterior(tmp_path, fake_mixer):
    player = _player(tmp_path, music_shuffle=False)
    player.start()
    first = player.current
    player.next()
    assert player.current != first and player.playing
    player.prev()
    assert player.current == first


def test_fin_de_tema_avanza_la_playlist(tmp_path, fake_mixer):
    player = _player(tmp_path, music_shuffle=False)
    player.start()
    first = player.current
    fake_mixer.music.busy = False  # el tema terminó solo
    assert player.handle_event(pygame.event.Event(MUSIC_END_EVENT))
    assert player.current != first
    assert player.playing


def test_evento_de_fin_viejo_se_ignora(tmp_path, fake_mixer):
    player = _player(tmp_path, music_shuffle=False)
    player.start()  # busy: ya hay otro tema sonando cuando llega el evento
    current = player.current
    player.handle_event(pygame.event.Event(MUSIC_END_EVENT))
    assert player.current == current


def test_secuencial_arranca_al_azar_y_sigue_en_orden(tmp_path, fake_mixer):
    player = _player(tmp_path, n_tracks=5, music_shuffle=False)
    order = player.order
    assert sorted(order) == [0, 1, 2, 3, 4]
    # rotación circular: cada tema va seguido del siguiente (mod 5)
    assert all((order[i] + 1) % 5 == order[(i + 1) % 5] for i in range(5))


def test_los_cambios_de_opciones_se_guardan(tmp_path, fake_mixer):
    player = _player(tmp_path)
    player.set_volume(0.5)
    player.set_shuffle(False)
    player.set_enabled(False)
    saved = load_settings(tmp_path / "settings.json")
    assert saved.music_volume == 0.5
    assert saved.music_shuffle is False
    assert saved.music_enabled is False
    assert fake_mixer.music.volume == 0.5
    assert not player.playing  # deshabilitarla la detuvo


def test_cambiar_carpeta_recarga_la_playlist(tmp_path, fake_mixer):
    player = _player(tmp_path)
    player.start()
    other = tmp_path / "other"
    other.mkdir()
    (other / "a.mp3").write_bytes(b"")
    player.set_folder(str(other))
    assert [t.name for t in player.tracks] == ["a.mp3"]
    assert player.playing  # estaba sonando: sigue con la carpeta nueva


def test_duck_baja_y_restaura_el_volumen(tmp_path, fake_mixer):
    player = _player(tmp_path, music_volume=0.8)
    player.start()
    assert fake_mixer.music.volume == 0.8
    player.duck()
    assert fake_mixer.music.volume == pytest.approx(0.8 * MusicPlayer.DUCK_FACTOR)
    player.duck()  # idempotente: no baja de nuevo
    assert fake_mixer.music.volume == pytest.approx(0.8 * MusicPlayer.DUCK_FACTOR)
    player.unduck()
    assert fake_mixer.music.volume == 0.8
    player.unduck()  # idempotente
    assert fake_mixer.music.volume == 0.8


def test_cambiar_volumen_mientras_esta_agachado(tmp_path, fake_mixer):
    player = _player(tmp_path, music_volume=0.8)
    player.start()
    player.duck()
    player.set_volume(0.4)  # el cambio respeta el agachado
    assert fake_mixer.music.volume == pytest.approx(0.4 * MusicPlayer.DUCK_FACTOR)
    player.unduck()
    assert fake_mixer.music.volume == 0.4


def test_settings_roundtrip(tmp_path):
    settings = Settings(
        music_enabled=False, music_volume=0.3, music_folder="x", music_shuffle=False
    )
    save_settings(settings, tmp_path / "s.json")
    assert load_settings(tmp_path / "s.json") == settings


def test_parse_resolution():
    from wom.ui.video import DEFAULT_RESOLUTION, parse_resolution

    assert parse_resolution("1920x1080") == (1920, 1080)
    assert parse_resolution("1280X720") == (1280, 720)  # mayúscula también
    assert parse_resolution("basura") == DEFAULT_RESOLUTION  # inválido: default


def test_settings_rotos_o_ausentes_usan_defaults(tmp_path):
    assert load_settings(tmp_path / "no_existe.json") == Settings()
    broken = tmp_path / "roto.json"
    broken.write_text("{ esto no es json", encoding="utf-8")
    assert load_settings(broken) == Settings()
