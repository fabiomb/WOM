"""Carga y escalado de los PNG de data/assets/.

Si falta un archivo se genera un cuadrado de color como fallback, así la UI
nunca rompe por un asset ausente (correr tools/gen_placeholders.py los crea).

Sobre los tiles de terreno/agua se aplica el estilo "pergamino vintage": un
tinte sepia (multiply) los unifica en un look de mapa antiguo, y de cada
terreno seco se hornean varias variantes orientadas/con brillo propio para
romper la repetición (el renderer elige una por tile, ver wom/ui/texture.py).
"""

from __future__ import annotations

from pathlib import Path

import pygame

from wom.core.worldmap import Terrain
from wom.paths import resource_root
from wom.ui import theme
from wom.ui.texture import FLIP_COMBOS
from wom.ui.tiling import WATER_CORNER_VARIANTS, WATER_VARIANTS

ASSETS_DIR = resource_root() / "data" / "assets"

UNIT_IDS = ("partisano", "soldado", "caballero", "arquero")
ICON_IDS = (
    "fort", "town", "flag", "flag_red", "flag_blue", "flag_green", "flag_yellow", "cross"
)
FALLBACK_COLOR = (200, 0, 200)

# Sprites de animación por clase (subcarpetas de data/assets/). Cada estado es
# una lista de frames; una clase sin entrada usa su sprite estático (`units`).
# Por ahora solo el soldado; las demás clases caen al PNG único de siempre.
UNIT_ANIMATIONS: dict[str, dict[str, list[str]]] = {
    "soldado": {
        "idle": ["soldado/soldado"],
        "walk": ["soldado/soldado-caminando-1", "soldado/soldado-caminando-2"],
        "attack": [
            "soldado/soldado-atacando-1",
            "soldado/soldado-atacando-2",
            "soldado/soldado-atacando-3",
        ],
        "death": ["soldado/soldado-muerto-1", "soldado/soldado-muerto-2"],
    },
    "caballero": {
        "idle": ["caballero/caballero"],
        "walk": ["caballero/caballero-caminando-1", "caballero/caballero-caminando-2"],
        "attack": [
            "caballero/caballero-atacando-1",
            "caballero/caballero-atacando-2",
            "caballero/caballero-atacando-3",
        ],
        "death": ["caballero/caballero-muerto-1", "caballero/caballero-muerto-2"],
    },
}

# Lados/esquinas de las máscaras de borde (autotiling de terreno seco). Se
# derivan por rotación/espejo de dos máscaras base (edge_mask, corner_mask).
EDGE_SIDES = ("n", "s", "e", "w")
CORNER_SIDES = ("ne", "nw", "se", "sw")


class Assets:
    """Sprites escalados al tamaño de tile de la partida en pantalla."""

    def __init__(self, tile_size: int):
        self.tile_size = tile_size
        unit_size = max(8, int(tile_size * 0.72))
        self.unit_size = unit_size  # tamaño de los sprites de unidad (para animar)
        icon_size = max(8, int(tile_size * 0.8))
        # Terreno seco: sepia + variantes de estilo (orientación/brillo) por
        # tile. `terrain` guarda la base sepia (paleta, fallback); `terrain_styles`
        # las variantes que el renderer reparte por celda.
        self.terrain = {}
        self.terrain_styles: dict[Terrain, list[pygame.Surface]] = {}
        for t in Terrain:
            # Los puentes son overlays transparentes: solo se tiñen (preservan
            # alfa). El resto son tiles opacos: se desaturan + tiñen.
            transparent = t in (Terrain.BRIDGE_H, Terrain.BRIDGE_V)
            base = (_tint if transparent else _age)(_load(t.value, tile_size))
            self.terrain[t] = base
            self.terrain_styles[t] = _style_variants(base)
        # Variantes de costa del agua (autotiling, tiles opacos): desat+sepia,
        # sin orientar (el autotiling ya es direccional).
        self.water = {name: _age(_load(name, tile_size)) for name in WATER_VARIANTS}
        # Overlays de esquina (transparentes): solo tinte.
        self.water_corners = {
            name: _tint(_load(name, tile_size)) for name in WATER_CORNER_VARIANTS
        }
        # Máscaras de borde para fundir terrenos secos (alfa, sin sepia).
        self.edge_masks = _edge_masks(tile_size)
        self.units = {u: _load(u, unit_size) for u in UNIT_IDS}
        self.icons = {i: _load(i, icon_size) for i in ICON_IDS}


def _sepia_color(strength: float) -> tuple[int, int, int]:
    """Color de multiply = lerp(blanco, SEPIA_TINT, strength)."""
    return tuple(int(255 + (c - 255) * strength) for c in theme.SEPIA_TINT)


def _age(surface: pygame.Surface) -> pygame.Surface:
    """Envejece un tile OPACO: lo desatura hacia gris y lo vira a sepia.

    Se hornea una vez por tamaño de zoom (sin costo por frame). Trabaja sobre
    una copia opaca (`convert`) para poder mezclar el gris con `set_alpha`.
    """
    out = surface.convert() if pygame.display.get_surface() else surface.copy()
    desat = theme.SEPIA_DESAT
    if desat > 0:
        gray = pygame.transform.grayscale(out)
        gray.set_alpha(int(255 * desat))
        out.blit(gray, (0, 0))  # mezcla hacia gris según desat
    if theme.SEPIA_STRENGTH > 0:
        out.fill(_sepia_color(theme.SEPIA_STRENGTH), special_flags=pygame.BLEND_RGB_MULT)
    return out


def _tint(surface: pygame.Surface) -> pygame.Surface:
    """Vira a sepia un sprite TRANSPARENTE (overlay/puente) preservando el alfa
    (multiply RGBA con alfa 255 = el alfa del sprite no cambia)."""
    if theme.SEPIA_STRENGTH <= 0:
        return surface
    out = surface.copy()
    out.fill((*_sepia_color(theme.SEPIA_STRENGTH), 255), special_flags=pygame.BLEND_RGBA_MULT)
    return out


def _adjust_brightness(surface: pygame.Surface, factor: float) -> pygame.Surface:
    """Oscurece (factor<1, multiply) o aclara (factor>1, add) un sprite."""
    if factor == 1.0:
        return surface
    out = surface.copy()
    if factor < 1.0:
        g = max(0, min(255, int(255 * factor)))
        out.fill((g, g, g, 255), special_flags=pygame.BLEND_RGBA_MULT)
    else:
        a = max(0, min(255, int(255 * (factor - 1.0))))
        out.fill((a, a, a, 0), special_flags=pygame.BLEND_RGBA_ADD)
    return out


def _style_variants(base: pygame.Surface) -> list[pygame.Surface]:
    """Hornea N variantes de un tile seco: cada una con su espejo y un brillo
    levemente distinto, para que el mismo terreno no se vea repetido."""
    count = max(1, theme.TILE_STYLE_VARIANTS)
    jitter = theme.TILE_BRIGHTNESS_JITTER
    # Brillos repartidos en [1-jitter, 1+jitter] (el primero es neutro).
    if count == 1:
        factors = [1.0]
    else:
        factors = [
            1.0 - jitter + 2 * jitter * (i / (count - 1)) for i in range(count)
        ]
    variants = []
    for i in range(count):
        flip_x, flip_y = FLIP_COMBOS[i % len(FLIP_COMBOS)]
        surf = pygame.transform.flip(base, flip_x, flip_y)
        variants.append(_adjust_brightness(surf, factors[i]))
    return variants


def _edge_masks(size: int) -> dict[str, pygame.Surface]:
    """Máscaras de borde escaladas a `size`, derivadas por rotación/espejo de
    dos máscaras base (edge_mask = banda al norte, corner_mask = esquina NE)."""
    edge = _load_mask("edge_mask", size)
    corner = _load_mask("corner_mask", size)
    masks = {
        "n": edge,
        "s": pygame.transform.rotate(edge, 180),
        "e": pygame.transform.rotate(edge, -90),  # la banda pasa al lado este
        "w": pygame.transform.rotate(edge, 90),
        "ne": corner,
        "nw": pygame.transform.flip(corner, True, False),
        "se": pygame.transform.flip(corner, False, True),
        "sw": pygame.transform.flip(corner, True, True),
    }
    return masks


def _scale(image: pygame.Surface, size: int, smooth: bool) -> pygame.Surface:
    """Escala a `size`×`size`. `smooth` usa smoothscale (mejor para reducir arte
    de alta resolución), con caída a scale si el formato no lo permite."""
    if smooth:
        try:
            return pygame.transform.smoothscale(image, (size, size))
        except (ValueError, pygame.error):
            pass
    return pygame.transform.scale(image, (size, size))


def _load(name: str, size: int, *, smooth: bool = False) -> pygame.Surface:
    path = ASSETS_DIR / f"{name}.png"
    if path.exists():
        image = pygame.image.load(str(path))
        if pygame.display.get_surface() is not None:
            image = image.convert_alpha()
        return _scale(image, size, smooth)
    surface = pygame.Surface((size, size))
    surface.fill(FALLBACK_COLOR)
    return surface


# Cache de frames de animación por (clase, estado, tamaño, flip). Se hornea una
# vez por tamaño (nivel de zoom / profundidad), como el resto de los sprites.
_frame_cache: dict[tuple[str, str, int, bool], list[pygame.Surface]] = {}


def has_unit_animation(class_id: str) -> bool:
    """La clase tiene sprites de animación (si no, se usa el sprite estático)."""
    return class_id in UNIT_ANIMATIONS


def unit_frame_count(class_id: str, state: str) -> int:
    """Cantidad de frames de un estado (sin cargarlos): para elegir una pose."""
    anim = UNIT_ANIMATIONS.get(class_id)
    return len(anim[state]) if anim is not None and state in anim else 0


def unit_frames(
    class_id: str, state: str, size: int, *, flip: bool = False
) -> list[pygame.Surface]:
    """Frames de un estado de animación (idle/walk/attack/death) escalados a
    `size`. `flip` los espeja horizontalmente (el sprite mira a la derecha por
    defecto; se espeja para el bando que encara hacia la izquierda). Lista vacía
    si la clase no anima o el estado no existe. Todo cacheado por (clase, estado,
    tamaño, flip)."""
    anim = UNIT_ANIMATIONS.get(class_id)
    if anim is None or state not in anim:
        return []
    key = (class_id, state, size, flip)
    frames = _frame_cache.get(key)
    if frames is None:
        if flip:
            base = unit_frames(class_id, state, size, flip=False)
            frames = [pygame.transform.flip(f, True, False) for f in base]
        else:
            frames = [_load(name, size, smooth=True) for name in anim[state]]
        _frame_cache[key] = frames
    return frames


def _load_mask(name: str, size: int) -> pygame.Surface:
    """Carga una máscara alfa (RGBA). Si falta, una banda de gradiente al norte
    (suaviza igual el borde, aunque sin el arte fino)."""
    path = ASSETS_DIR / f"{name}.png"
    if path.exists():
        image = pygame.image.load(str(path)).convert_alpha()
        return pygame.transform.scale(image, (size, size))
    surface = pygame.Surface((size, size), pygame.SRCALPHA)
    band = max(1, size // 2)
    for y in range(band):
        alpha = int(255 * (1 - y / band))
        pygame.draw.line(surface, (255, 255, 255, alpha), (0, y), (size, y))
    return surface


def load_scaled(name: str, size: int) -> pygame.Surface:
    """Carga un sprite de data/assets/ escalado a `size`×`size`.

    Igual que el escalado interno de `Assets`, pero a un tamaño arbitrario:
    útil fuera del mapa (p. ej. la ayuda de F1, que muestra los mismos tiles).
    Si el archivo falta devuelve un cuadrado de color (fallback).
    """
    return _load(name, size)


def load_image(name: str) -> pygame.Surface | None:
    """Carga un PNG de data/assets/ a su tamaño original, o None si falta.

    Útil para imágenes que no son sprites de tile (p. ej. la ilustración de
    victoria/derrota de la pantalla de fin de partida).
    """
    path = ASSETS_DIR / f"{name}.png"
    if not path.exists():
        return None
    image = pygame.image.load(str(path))
    if pygame.display.get_surface() is not None:
        image = image.convert_alpha()
    return image
