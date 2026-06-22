"""Tests de la variación determinista por tile (wom/ui/texture.py, puro)."""

from collections import Counter

from wom.ui.texture import (
    FLIP_COMBOS,
    brightness,
    orientation,
    style_index,
    tile_hash,
    variant_index,
)


def test_hash_determinista_y_con_salt_independiente():
    assert tile_hash(3, 7) == tile_hash(3, 7)  # estable por celda
    assert tile_hash(3, 7) != tile_hash(7, 3)  # no es simétrico en (x, y)
    # distintos salts dan sorteos independientes para la misma celda
    assert tile_hash(3, 7, salt=1) != tile_hash(3, 7, salt=2)


def test_hash_bien_repartido():
    """El hash low-bit no se sesga groseramente (rompe la repetición)."""
    counts = Counter(tile_hash(x, y) & 3 for x in range(40) for y in range(40))
    total = sum(counts.values())
    for bucket in range(4):
        share = counts[bucket] / total
        assert 0.18 < share < 0.32, f"bucket {bucket}: {share:.2f}"


def test_orientacion_en_rango_y_estable():
    for x in range(10):
        for y in range(10):
            idx = orientation(x, y)
            assert 0 <= idx < len(FLIP_COMBOS)
            assert idx == orientation(x, y)


def test_variant_index_respeta_el_conteo():
    assert variant_index(2, 2, 1) == 0   # una sola variante
    assert variant_index(2, 2, 0) == 0   # sin variantes
    for x in range(20):
        assert 0 <= variant_index(x, 0, 3) < 3
    assert 0 <= style_index(5, 9, 4) < 4
    assert style_index(5, 9, 1) == 0


def test_brillo_en_rango():
    assert brightness(1, 1, 0.0) == 1.0  # sin jitter
    for x in range(20):
        for y in range(20):
            b = brightness(x, y, 0.1)
            assert 0.9 - 1e-9 <= b <= 1.1 + 1e-9
