"""Reproducción de clips .mp4 cortos sobre pygame, decodificados con ffmpeg.

pygame no reproduce video; este módulo decodifica los frames con un subproceso
ffmpeg (rawvideo rgb24 por pipe) en un hilo de fondo y reproduce el audio
extraído por un canal de `pygame.mixer`. Lo usa la pantalla de fin de partida
para mostrar `victory.mp4`/`defeat.mp4` ~5s y luego fundir a la imagen fija.

Resiliente por diseño (como la música): si ffmpeg no está disponible, el
archivo falta, o el mixer no inicializó, `available` queda False y el llamador
muestra la imagen sin más. **No se usa en modo headless** (SDL_VIDEODRIVER=
dummy): los tests y las capturas muestran la imagen sin lanzar subprocesos.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import threading
from functools import lru_cache
from pathlib import Path

import pygame
from pygame import mixer

from wom.paths import resource_root

# Tiempo del fundido del último frame del video hacia la imagen fija (segundos).
FADE_SECONDS = 0.7


@lru_cache(maxsize=1)
def ffmpeg_binary() -> str | None:
    """Ruta al ejecutable ffmpeg a usar, o None si no hay ninguno.

    Orden de preferencia, para que funcione tanto en desarrollo como en los
    builds de PyInstaller:
      1) un binario **empaquetado** en `data/bin/` (lo que se distribuye);
      2) el que provee el paquete `imageio-ffmpeg` (estático, si está instalado);
      3) un `ffmpeg` del PATH del sistema (desarrollo).
    """
    name = "ffmpeg.exe" if sys.platform == "win32" else "ffmpeg"
    bundled = resource_root() / "data" / "bin" / name
    if bundled.exists():
        return str(bundled)
    try:
        import imageio_ffmpeg  # type: ignore

        exe = imageio_ffmpeg.get_ffmpeg_exe()
        if exe and Path(exe).exists():
            return exe
    except Exception:
        pass
    return shutil.which("ffmpeg")


def ffmpeg_available() -> bool:
    """True si hay algún binario ffmpeg disponible (empaquetado o del sistema)."""
    return ffmpeg_binary() is not None


# En Windows windowed (sin consola) evita el parpadeo de una ventana de consola
# al lanzar ffmpeg.
_NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0


def can_play_video() -> bool:
    """Condiciones para intentar reproducir un clip: display real (no headless)
    y ffmpeg disponible. En cualquier otro caso el llamador usa la imagen."""
    return os.environ.get("SDL_VIDEODRIVER") != "dummy" and ffmpeg_available()


def _read_frame(stream, n: int) -> bytes | None:
    """Lee exactamente `n` bytes del pipe (un frame). None al final del stream."""
    chunks = []
    remaining = n
    while remaining > 0:
        chunk = stream.read(remaining)
        if not chunk:
            return None  # fin del stream (último frame incompleto descartado)
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


class VideoClip:
    """Decodifica un .mp4 corto a frames en memoria y lo dibuja sobre pygame.

    El trabajo pesado (decodificar todos los frames + extraer y arrancar el
    audio) ocurre en un hilo; mientras tanto `draw` no pinta nada (se ve la
    imagen base del llamador). Cuando el audio arranca empieza a contar el
    tiempo y a revelar los frames, sincronizados con el sonido.
    """

    def __init__(self, path: str | Path, size: tuple[int, int], *, fps: int = 24,
                 volume: float = 0.85):
        self.size = (int(size[0]), int(size[1]))
        self.fps = fps
        self.available = False
        self.finished = False
        self._frames: list[bytes] = []
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._proc: subprocess.Popen | None = None
        self._thread: threading.Thread | None = None
        self._audio_path: Path | None = None
        self._channel = None
        self._sound = None
        self._started_ticks: int | None = None  # ticks al arrancar el audio
        self._surf_cache: tuple[int, pygame.Surface] | None = None

        path = Path(path)
        if not path.exists() or not can_play_video():
            return
        try:
            self._thread = threading.Thread(
                target=self._work, args=(path, volume), daemon=True
            )
            self._thread.start()
            self.available = True
        except Exception:
            self.close()

    # --- decodificación (hilo de fondo) -----------------------------------

    def _work(self, path: Path, volume: float) -> None:
        try:
            self._decode_frames(path)
            if self._stop.is_set():
                return
            self._start_audio(path, volume)
            # El reloj arranca con el audio (o ya, si no hubo audio): a partir
            # de acá `draw` revela los frames sincronizados.
            self._started_ticks = pygame.time.get_ticks()
        except Exception:
            pass  # cualquier fallo: el llamador ya muestra la imagen base

    def _decode_frames(self, path: Path) -> None:
        w, h = self.size
        cmd = [
            ffmpeg_binary(), "-nostdin", "-loglevel", "error", "-i", str(path),
            "-vf", f"scale={w}:{h}", "-r", str(self.fps),
            "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1",
        ]
        self._proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            creationflags=_NO_WINDOW,
        )
        frame_size = w * h * 3
        assert self._proc.stdout is not None
        while not self._stop.is_set():
            data = _read_frame(self._proc.stdout, frame_size)
            if data is None:
                break
            with self._lock:
                self._frames.append(data)
        self._proc.stdout.close()
        self._proc.wait()
        self._proc = None

    def _start_audio(self, path: Path, volume: float) -> None:
        """Extrae la pista de audio a un .ogg temporal y la reproduce en un
        canal propio del mixer (no toca la música de fondo)."""
        try:
            if not mixer.get_init():
                return
            fd, name = tempfile.mkstemp(suffix=".ogg", prefix="wom_clip_")
            os.close(fd)
            self._audio_path = Path(name)
            cmd = [
                ffmpeg_binary(), "-nostdin", "-loglevel", "error", "-y", "-i", str(path),
                "-vn", "-ac", "2", "-ar", "44100", str(self._audio_path),
            ]
            subprocess.run(
                cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=_NO_WINDOW,
            )
            if self._stop.is_set():
                return
            self._sound = mixer.Sound(str(self._audio_path))
            self._channel = self._sound.play()
            if self._channel is not None:
                self._channel.set_volume(volume)
        except Exception:
            self._sound = None
            self._channel = None

    @property
    def audible(self) -> bool:
        """True mientras el clip está sonando (entre el arranque del audio y el
        fin de su duración). Lo usa el llamador para agachar la música."""
        if self._started_ticks is None:
            return False
        with self._lock:
            n = len(self._frames)
        if n == 0:
            return False
        elapsed = (pygame.time.get_ticks() - self._started_ticks) / 1000.0
        return elapsed < n / self.fps

    # --- dibujo (hilo principal) ------------------------------------------

    def draw(self, surface: pygame.Surface, rect: pygame.Rect) -> None:
        """Pinta el frame actual sobre `rect`. Antes de que el audio arranque
        no dibuja nada (se ve la imagen base); pasados los ~5s funde el último
        frame hacia la imagen y, terminado el fundido, deja de pintar."""
        if self._started_ticks is None:
            return
        with self._lock:
            n = len(self._frames)
        if n == 0:
            return
        total = n / self.fps
        elapsed = (pygame.time.get_ticks() - self._started_ticks) / 1000.0
        if elapsed >= total + FADE_SECONDS:
            self.finished = True
            return
        idx = min(int(elapsed * self.fps), n - 1)
        frame = self._surface_at(idx)
        if frame is None:
            return
        if elapsed > total:  # fundido del último frame hacia la imagen fija
            alpha = max(0, int(255 * (1.0 - (elapsed - total) / FADE_SECONDS)))
            frame = frame.copy()
            frame.set_alpha(alpha)
        surface.blit(frame, rect.topleft)

    def _surface_at(self, idx: int) -> pygame.Surface | None:
        """Convierte (y cachea) el frame `idx` a Surface. La conversión se hace
        en el hilo principal; el hilo de fondo solo junta bytes crudos."""
        if self._surf_cache is not None and self._surf_cache[0] == idx:
            return self._surf_cache[1]
        with self._lock:
            if idx >= len(self._frames):
                return self._surf_cache[1] if self._surf_cache else None
            data = self._frames[idx]
        surf = pygame.image.frombuffer(data, self.size, "RGB")
        if pygame.display.get_surface() is not None:
            surf = surf.convert()
        self._surf_cache = (idx, surf)
        return surf

    # --- limpieza ----------------------------------------------------------

    def close(self) -> None:
        """Corta el decodificado, detiene el audio y borra el archivo temporal.
        Idempotente: seguro de llamar más de una vez."""
        self._stop.set()
        if self._channel is not None:
            try:
                self._channel.stop()
            except Exception:
                pass
            self._channel = None
        proc = self._proc
        if proc is not None:
            try:
                proc.terminate()
            except Exception:
                pass
        if self._thread is not None:
            self._thread.join(timeout=1.0)
            self._thread = None
        if self._audio_path is not None:
            try:
                self._audio_path.unlink()
            except OSError:
                pass
            self._audio_path = None
        self.available = False


class VideoBackground:
    """Fondo de pantalla en video, reproducido en bucle sin fin (sin audio).

    A diferencia de `VideoClip` (que carga un clip corto entero a memoria para
    fundirlo hacia una imagen), este decodifica **en streaming**: lanza ffmpeg
    con `-stream_loop -1` y un hilo lee de a un frame, guardando solo el último
    (memoria constante, sirve para videos largos como la portada del menú). El
    llamador dibuja la imagen de fallback debajo; `draw` la tapa con el frame
    actual cuando hay uno listo. Resiliente igual que `VideoClip`: sin ffmpeg,
    sin el archivo o en headless, `draw` no pinta nada y se ve la imagen.
    """

    def __init__(self, path: str | Path, size: tuple[int, int], *, fps: int = 24):
        self.size = (int(size[0]), int(size[1]))
        self.fps = max(1, fps)
        self.available = False
        self._latest: bytes | None = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._proc: subprocess.Popen | None = None
        self._thread: threading.Thread | None = None
        self._surf_cache: tuple[int, pygame.Surface] | None = None

        path = Path(path)
        if not path.exists() or not can_play_video():
            return
        try:
            self._thread = threading.Thread(target=self._work, args=(path,), daemon=True)
            self._thread.start()
            self.available = True
        except Exception:
            self.close()

    def _work(self, path: Path) -> None:
        """Hilo lector: decodifica en bucle y guarda el último frame, marcando
        el ritmo (~fps) para que el bucle corra a velocidad normal."""
        w, h = self.size
        cmd = [
            ffmpeg_binary(), "-nostdin", "-loglevel", "error", "-stream_loop", "-1",
            "-i", str(path), "-vf", f"scale={w}:{h}", "-r", str(self.fps),
            "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1",
        ]
        try:
            self._proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                creationflags=_NO_WINDOW,
            )
        except Exception:
            return
        frame_size = w * h * 3
        period = 1.0 / self.fps
        assert self._proc.stdout is not None
        while not self._stop.is_set():
            data = _read_frame(self._proc.stdout, frame_size)
            if data is None:
                break  # ffmpeg terminó/murió (con -stream_loop no debería)
            with self._lock:
                self._latest = data
            # El sleep frena la lectura; el pipe llena y ffmpeg se autolimita al
            # ritmo de reproducción (backpressure), sin acelerar el video.
            self._stop.wait(period)

    def draw(self, surface: pygame.Surface, rect: pygame.Rect) -> bool:
        """Pinta el frame actual sobre `rect`. Devuelve True si dibujó algo
        (False mientras aún no hay frame: el llamador deja ver la imagen)."""
        with self._lock:
            data = self._latest
        if data is None:
            return False
        key = id(data)
        if self._surf_cache is None or self._surf_cache[0] != key:
            surf = pygame.image.frombuffer(data, self.size, "RGB")
            if pygame.display.get_surface() is not None:
                surf = surf.convert()
            self._surf_cache = (key, surf)
        surface.blit(self._surf_cache[1], rect.topleft)
        return True

    def close(self) -> None:
        """Corta el decodificado y el subproceso ffmpeg. Idempotente."""
        self._stop.set()
        proc = self._proc
        if proc is not None:
            try:
                proc.terminate()
            except Exception:
                pass
        if self._thread is not None:
            self._thread.join(timeout=1.0)
            self._thread = None
        self._proc = None
        self.available = False
