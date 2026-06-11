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
    assert water_tile(world, (0, 1)) == "water_ns"  # canal: el puente es agua
    world_sin_puente = _world([
        "ppp",
        "wpw",
        "ppp",
    ])
    # 3 costas con salida al oeste (el borde del mapa cuenta como agua)
    assert water_tile(world_sin_puente, (0, 1)) == "water_u_w"


def test_canales_de_rio():
    horizontal = _world([
        "ppp",
        "www",
        "ppp",
    ])
    assert water_tile(horizontal, (1, 1)) == "water_ns"  # costa N y S
    vertical = _world([
        "pwp",
        "pwp",
        "pwp",
    ])
    assert water_tile(vertical, (1, 1)) == "water_ew"  # costa E y O


def test_agua_en_u_con_salida_a_cada_punto_cardinal():
    # lago en cruz: cada punta tiene tres costas y la salida hacia el centro
    world = _world([
        "ppppp",
        "ppwpp",
        "pwwwp",
        "ppwpp",
        "ppppp",
    ])
    assert water_tile(world, (2, 1)) == "water_u_s"  # salida al sur (al centro)
    assert water_tile(world, (2, 3)) == "water_u_n"
    assert water_tile(world, (1, 2)) == "water_u_e"
    assert water_tile(world, (3, 2)) == "water_u_w"
