"""Variación determinista por tile para romper la repetición del terreno.

Módulo puro (sin pygame, testeable headless). Da un hash entero estable por
coordenada de tile y, a partir de él, elige orientación (flip), variante de
sprite y un leve jitter de brillo. Todo es función de `(x, y)` — **nunca usa
`Game.rng`** —, así el render queda determinista sin tocar la simulación.
"""

from __future__ import annotations

_U32 = 0xFFFFFFFF


def tile_hash(x: int, y: int, salt: int = 0) -> int:
    """Hash entero de 32 bits estable para `(x, y)` (mezcla tipo splitmix).

    `salt` permite varios sorteos independientes para el mismo tile
    (orientación, variante, brillo) sin que se correlacionen.
    """
    h = (x * 0x1F1F1F1F) ^ (y * 0x85EBCA77) ^ (salt * 0xC2B2AE3D)
    h &= _U32
    h ^= h >> 16
    h = (h * 0x7FEB352D) & _U32
    h ^= h >> 15
    h = (h * 0x846CA68B) & _U32
    h ^= h >> 16
    return h & _U32


# Orientaciones permitidas (flip_x, flip_y): identidad y espejo HORIZONTAL.
# El espejo vertical queda excluido a propósito: muchos tiles tienen dibujos
# con sombreado/orientación que solo se ven bien sobre el eje vertical, así que
# voltearlos en horizontal está OK pero en vertical no.
FLIP_COMBOS = ((False, False), (True, False))


def orientation(x: int, y: int) -> int:
    """Índice de la orientación (espejo horizontal) del tile, estable por celda."""
    return tile_hash(x, y, salt=1) % len(FLIP_COMBOS)


def variant_index(x: int, y: int, count: int) -> int:
    """Índice 0..count-1 de la variante de sprite a usar (0 si count<=1)."""
    if count <= 1:
        return 0
    return tile_hash(x, y, salt=2) % count


def brightness(x: int, y: int, amount: float) -> float:
    """Factor de brillo en [1-amount, 1+amount] estable por celda.

    Con amount 0 devuelve 1.0 (sin jitter).
    """
    if amount <= 0:
        return 1.0
    frac = (tile_hash(x, y, salt=3) % 1000) / 999.0  # 0..1
    return 1.0 - amount + frac * (2.0 * amount)


def style_index(x: int, y: int, count: int) -> int:
    """Índice 0..count-1 del 'estilo' compuesto (orientación+brillo) del tile.

    El renderer hornea `count` variantes ya orientadas y con brillo propio; este
    índice elige cuál usar por celda, estable y bien repartido.
    """
    if count <= 1:
        return 0
    return tile_hash(x, y, salt=4) % count
