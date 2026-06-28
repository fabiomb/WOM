"""Tests del reproductor de clips de fin de partida (wom/ui/videoclip.py).

No decodifican video de verdad (eso necesita ffmpeg + display real): cubren la
resolución del binario y la degradación headless, que es donde vive la lógica.
"""

import os

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import wom.ui.videoclip as videoclip
from wom.ui.videoclip import VideoClip, can_play_video, ffmpeg_binary


def test_headless_no_reproduce(tmp_path):
    """Con SDL dummy (tests/capturas) nunca se intenta el video: el llamador
    muestra la imagen fija."""
    assert os.environ.get("SDL_VIDEODRIVER") == "dummy"
    assert can_play_video() is False
    # Construir un clip en headless no lanza ffmpeg y queda no disponible.
    clip = VideoClip(tmp_path / "no_existe.mp4", (64, 64))
    assert clip.available is False
    assert clip.audible is False
    clip.close()  # idempotente y seguro aunque nunca arrancó


def test_resolver_prefiere_el_binario_empaquetado(tmp_path, monkeypatch):
    """data/bin/ffmpeg[.exe] (lo que se distribuye) gana sobre imageio/PATH."""
    ffmpeg_binary.cache_clear()
    bindir = tmp_path / "data" / "bin"
    bindir.mkdir(parents=True)
    name = "ffmpeg.exe" if videoclip.sys.platform == "win32" else "ffmpeg"
    bundled = bindir / name
    bundled.write_bytes(b"")
    monkeypatch.setattr(videoclip, "resource_root", lambda: tmp_path)
    try:
        assert ffmpeg_binary() == str(bundled)
    finally:
        ffmpeg_binary.cache_clear()  # no contaminar otros tests


def test_resolver_sin_ffmpeg_es_none(tmp_path, monkeypatch):
    """Sin binario empaquetado, sin imageio y sin ffmpeg en el PATH: None."""
    ffmpeg_binary.cache_clear()
    monkeypatch.setattr(videoclip, "resource_root", lambda: tmp_path)  # sin data/bin
    monkeypatch.setattr(videoclip.shutil, "which", lambda _name: None)  # sin PATH
    monkeypatch.setitem(__import__("sys").modules, "imageio_ffmpeg", None)  # import falla
    try:
        assert ffmpeg_binary() is None
    finally:
        ffmpeg_binary.cache_clear()
