"""Genera los PNG placeholder en data/assets/ para el arte final.

Cada placeholder se guarda siempre como `_<nombre>.png` (referencia) y como
`<nombre>.png` SOLO si ese archivo no existe: correr el script nunca pisa
arte final ya instalado.

Convención de tamaños (el arte final reemplaza archivos con el mismo
nombre y dimensiones):
- Tiles de terreno: 64x64 px (plains, forest, mountain, water, y las 15
  variantes de costa del agua: water_n/s/e/w, water_ne/nw/se/sw, los canales
  water_ns/water_ew, las U water_u_n/s/e/w y water_single — ver
  wom/ui/tiling.py). Los 4 overlays de esquina (water_corner_ne/nw/se/sw) y
  los 2 puentes (bridge_h/bridge_v) son 64x64 CON transparencia: se dibujan
  sobre el tile de agua (la esquina suaviza una punta de tierra en diagonal;
  el puente deja ver la orilla por debajo).
- Unidades/ejércitos: 48x48 px (una por clase: partisano, soldado,
  caballero, arquero) — color de fondo según jugador se aplica en runtime.
- Íconos: 32x32 px (fort, town, cruz de ejército muerto, y las banderas:
  flag_red para el jugador 0, flag_blue para el 1, flag gris para neutrales)

Uso:
    python tools/gen_placeholders.py
"""

from __future__ import annotations

from pathlib import Path

import pygame

ASSETS_DIR = Path(__file__).resolve().parents[1] / "data" / "assets"

TILES = {  # 64x64, color plano + letra
    "plains": (110, 160, 70),
    "forest": (40, 100, 45),
    "mountain": (130, 120, 110),
    "water": (50, 90, 160),
}
UNITS = {  # 48x48
    "partisano": (200, 170, 60),
    "soldado": (170, 170, 180),
    "caballero": (190, 120, 60),
    "arquero": (100, 170, 130),
}
ICONS = {  # 32x32
    "fort": (90, 80, 70),
    "town": (180, 150, 100),
    "cross": (40, 40, 40),
}
FLAGS = {  # 32x32, mástil + paño del color del dueño (ver theme.PLAYER_COLORS)
    "flag": (150, 150, 150),        # sitio neutral
    "flag_red": (210, 70, 60),      # jugador 0 (humano)
    "flag_blue": (70, 110, 210),    # jugador 1
    "flag_green": (80, 175, 80),    # jugador 2
    "flag_yellow": (225, 195, 70),  # jugador 3
}


# Variantes de costa del agua: nombre → lados con tierra (banda de orilla).
WATER_EDGES = {
    "water_n": ("n",),
    "water_s": ("s",),
    "water_e": ("e",),
    "water_w": ("w",),
    "water_ne": ("n", "e"),
    "water_nw": ("n", "w"),
    "water_se": ("s", "e"),
    "water_sw": ("s", "w"),
    "water_ns": ("n", "s"),      # canal horizontal (río E-O)
    "water_ew": ("e", "w"),      # canal vertical (río N-S)
    "water_u_n": ("s", "e", "w"),  # en U con salida al norte
    "water_u_s": ("n", "e", "w"),  # en U con salida al sur
    "water_u_e": ("n", "s", "w"),  # en U con salida al este
    "water_u_w": ("n", "s", "e"),  # en U con salida al oeste
    "water_single": ("n", "s", "e", "w"),
}
SHORE_COLOR = (205, 185, 140)  # arena de la orilla
BRIDGE_WOOD = (130, 90, 50)
BRIDGE_WOOD_DARK = (95, 65, 35)


def _save(surface: pygame.Surface, name: str, out_dir: Path) -> None:
    """Guarda `_<name>.png` siempre y `<name>.png` solo si no existe
    (no pisa arte final ya instalado)."""
    pygame.image.save(surface, str(out_dir / f"_{name}.png"))
    final = out_dir / f"{name}.png"
    if not final.exists():
        pygame.image.save(surface, str(final))


def _make(name: str, size: int, color: tuple[int, int, int], out_dir: Path) -> None:
    surface = pygame.Surface((size, size))
    surface.fill(color)
    pygame.draw.rect(surface, (0, 0, 0), surface.get_rect(), 2)
    font = pygame.font.SysFont(None, size // 2)
    letter = font.render(name[0].upper(), True, (255, 255, 255))
    surface.blit(letter, letter.get_rect(center=surface.get_rect().center))
    _save(surface, name, out_dir)


def _make_flag(name: str, size: int, color: tuple[int, int, int], out_dir: Path) -> None:
    """Bandera con fondo transparente: mástil y paño del color del dueño."""
    surface = pygame.Surface((size, size), pygame.SRCALPHA)
    pole_x = size // 4
    pygame.draw.line(surface, (70, 55, 40), (pole_x, size // 8),
                     (pole_x, size - size // 8), max(2, size // 12))
    pennant = [
        (pole_x + 1, size // 8),
        (size - size // 8, size // 4 + size // 16),
        (pole_x + 1, size // 2),
    ]
    pygame.draw.polygon(surface, color, pennant)
    pygame.draw.polygon(surface, (0, 0, 0), pennant, 1)
    _save(surface, name, out_dir)


def _water_base(size: int) -> pygame.Surface:
    """Base de agua: el arte real de water.png si existe (los bordes y
    puentes momentáneos pegan con el resto del mapa), si no color plano."""
    art = ASSETS_DIR / "water.png"
    if art.exists():
        return pygame.transform.smoothscale(pygame.image.load(str(art)), (size, size))
    surface = pygame.Surface((size, size))
    surface.fill(TILES["water"])
    return surface


def _make_water_edge(name: str, sides: tuple[str, ...], size: int, out_dir: Path) -> None:
    """Tile de agua con banda de orilla en los lados que tocan tierra."""
    surface = _water_base(size)
    band = max(4, size // 7)
    rects = {
        "n": pygame.Rect(0, 0, size, band),
        "s": pygame.Rect(0, size - band, size, band),
        "e": pygame.Rect(size - band, 0, band, size),
        "w": pygame.Rect(0, 0, band, size),
    }
    for side in sides:
        pygame.draw.rect(surface, SHORE_COLOR, rects[side])
    _save(surface, name, out_dir)


# Esquina → diagonal (Δx, Δy) donde asoma la tierra (1 = lado max del eje).
_CORNER_DIAG = {
    "water_corner_ne": (1, -1),
    "water_corner_nw": (-1, -1),
    "water_corner_se": (1, 1),
    "water_corner_sw": (-1, 1),
}


def _make_water_corner(name: str, size: int, out_dir: Path) -> None:
    """Overlay de esquina transparente: una mancha de orilla en el vértice por
    donde asoma la tierra en diagonal. Se pinta sobre el tile de agua."""
    surface = pygame.Surface((size, size), pygame.SRCALPHA)
    band = max(4, size // 7)
    dx, dy = _CORNER_DIAG[name]
    x = size - band if dx > 0 else 0
    y = size - band if dy > 0 else 0
    pygame.draw.rect(surface, SHORE_COLOR, pygame.Rect(x, y, band, band))
    _save(surface, name, out_dir)


def _make_bridge(name: str, horizontal: bool, size: int, out_dir: Path) -> None:
    """Puente de madera transparente: solo los tablones, sin agua de fondo
    (el render dibuja el agua autotileada por debajo)."""
    surface = pygame.Surface((size, size), pygame.SRCALPHA)
    span = max(8, size // 2)  # ancho de la pasarela
    if horizontal:
        deck = pygame.Rect(0, (size - span) // 2, size, span)
    else:
        deck = pygame.Rect((size - span) // 2, 0, span, size)
    pygame.draw.rect(surface, BRIDGE_WOOD, deck)
    step = max(4, size // 8)
    if horizontal:  # tablones verticales (cruzan la pasarela)
        for x in range(0, size, step):
            pygame.draw.line(surface, BRIDGE_WOOD_DARK, (x, deck.top), (x, deck.bottom - 1))
        pygame.draw.line(surface, BRIDGE_WOOD_DARK, (0, deck.top), (size, deck.top), 2)
        pygame.draw.line(surface, BRIDGE_WOOD_DARK, (0, deck.bottom - 2), (size, deck.bottom - 2), 2)
    else:
        for y in range(0, size, step):
            pygame.draw.line(surface, BRIDGE_WOOD_DARK, (deck.left, y), (deck.right - 1, y))
        pygame.draw.line(surface, BRIDGE_WOOD_DARK, (deck.left, 0), (deck.left, size), 2)
        pygame.draw.line(surface, BRIDGE_WOOD_DARK, (deck.right - 2, 0), (deck.right - 2, size), 2)
    _save(surface, name, out_dir)


def main() -> None:
    pygame.init()
    ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    for group, size in ((TILES, 64), (UNITS, 48), (ICONS, 32)):
        for name, color in group.items():
            _make(name, size, color, ASSETS_DIR)
    for name, sides in WATER_EDGES.items():
        _make_water_edge(name, sides, 64, ASSETS_DIR)
    for name in _CORNER_DIAG:
        _make_water_corner(name, 64, ASSETS_DIR)
    _make_bridge("bridge_h", True, 64, ASSETS_DIR)
    _make_bridge("bridge_v", False, 64, ASSETS_DIR)
    for name, color in FLAGS.items():
        _make_flag(name, 32, color, ASSETS_DIR)
    print(f"Placeholders generados en {ASSETS_DIR}")
    pygame.quit()


if __name__ == "__main__":
    main()
