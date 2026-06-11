"""Tests del autotiling del agua (wom/ui/tiling.py, puro sin pygame)."""

from wom.core.worldmap import CHAR_TO_TERRAIN, WorldMap
from wom.ui.tiling import water_tile


def _world(rows: list[str]) -> WorldMap:
    """Mapa desde strings ('w' agua, 'p' llanura, 'h'/'v' puentes)."""
    tiles = [[CHAR_TO_TERRAIN[c] for c in row] for row in rows]
    return WorldMap(width=len(rows[0]), height=len(rows), tiles=tiles)


def test_lago_bordes_esquinas_y_centro():
    world = _world([
        "ppppp",
        "pwwwp",
        "pwwwp",
        "pwwwp",
        "ppppp",
    ])
    assert water_tile(world, (2, 2)) == "water"  # centro: agua pura
    assert water_tile(world, (2, 1)) == "water_n"
    assert water_tile(world, (2, 3)) == "water_s"
    assert water_tile(world, (3, 2)) == "water_e"
    assert water_tile(world, (1, 2)) == "water_w"
    assert water_tile(world, (1, 1)) == "water_nw"
    assert water_tile(world, (3, 1)) == "water_ne"
    assert water_tile(world, (1, 3)) == "water_sw"
    assert water_tile(world, (3, 3)) == "water_se"


def test_charco_aislado():
    world = _world([
        "ppp",
        "pwp",
        "ppp",
    ])
    assert water_tile(world, (1, 1)) == "water_single"


def test_el_borde_del_mapa_cuenta_como_agua():
    world = _world(["wp"])  # agua en la esquina: solo hay costa al este
    assert water_tile(world, (0, 0)) == "water_e"


def test_puente_cuenta_como_agua():
    # el río sigue por abajo del puente: no se dibuja orilla contra él
    world = _world([
        "ppp",
        "whw",
        "ppp",
    ])
    assert water_tile(world, (0, 1)) in ("water_n", "water_s")  # canal: fallback
    world_sin_puente = _world([
        "ppp",
        "wpw",
        "ppp",
    ])
    # 3 costas (sin asset exacto): cae a la esquina más parecida
    assert water_tile(world_sin_puente, (0, 1)) == "water_ne"


def test_canal_angosto_usa_fallback_determinista():
    world = _world([
        "ppp",
        "www",
        "ppp",
    ])
    # canal horizontal (costa N y S, sin asset exacto): variante consistente
    assert water_tile(world, (1, 1)) == water_tile(world, (0, 1)) is not None
