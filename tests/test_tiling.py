"""Tests del autotiling del agua (wom/ui/tiling.py, puro sin pygame)."""

from wom.core.worldmap import CHAR_TO_TERRAIN, Terrain, WorldMap
from wom.ui.tiling import dry_corners, dry_edges, ink_group, water_corners, water_tile


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


def test_esquina_punta_de_tierra_en_diagonal():
    # tierra que asoma por la diagonal NW: los dos ortogonales son agua
    world = _world([
        "ppwww",
        "ppwww",
        "wwwww",
        "wwwww",
    ])
    assert water_corners(world, (2, 2)) == {"water_corner_nw"}
    # agua interior pura (borde del mapa = agua): sin esquinas
    assert water_corners(world, (3, 3)) == frozenset()


def test_no_hay_esquina_si_el_ortogonal_ya_es_costa():
    # (2,1) tiene costa recta al norte y al este: nada que suavizar en diagonal
    world = _world([
        "ppwww",
        "ppwww",
        "wwwww",
        "wwwww",
    ])
    assert water_corners(world, (2, 1)) == frozenset()


def test_esquina_convive_con_orilla_recta():
    # costa recta al N y al E (water_ne) + una punta de tierra en el SO
    world = _world([
        "ppp",
        "wwp",
        "pwp",
    ])
    assert water_tile(world, (1, 1)) == "water_ne"
    assert water_corners(world, (1, 1)) == {"water_corner_sw"}


# --- autotiling de terreno seco (bordes fluidos) --------------------------


def test_dry_edges_el_terreno_dominante_derrama_sobre_el_menor():
    # bosque (prioridad alta) al este de una llanura: la llanura recibe el borde
    world = _world([
        "pf",
        "pp",
    ])
    assert dry_edges(world, (0, 0)) == [("e", Terrain.FOREST)]
    # el bosque, más dominante, no recibe borde de la llanura
    assert dry_edges(world, (1, 0)) == []


def test_dry_edges_ignora_el_agua():
    # el agua la maneja el autotiling de costa: no genera borde seco
    world = _world([
        "pw",
        "pp",
    ])
    assert dry_edges(world, (0, 0)) == []
    assert dry_edges(world, (1, 0)) == []  # el agua tampoco recibe bordes secos


def test_dry_corners_punta_en_diagonal():
    # bosque solo en la diagonal NE de la llanura (ortogonales son llanura)
    world = _world([
        "ppf",
        "ppp",
        "ppp",
    ])
    assert dry_corners(world, (1, 1)) == [("ne", Terrain.FOREST)]
    # si el ortogonal ya es bosque, no hay esquina (lo cubre el borde recto)
    world2 = _world([
        "pff",
        "ppp",
        "ppp",
    ])
    assert dry_corners(world2, (1, 1)) == []


def test_ink_group_agrupa_variantes():
    # las variantes livianas comparten grupo con su feature; el puente, con agua
    assert ink_group(Terrain.FOREST) == ink_group(Terrain.FOREST_LIGHT)
    assert ink_group(Terrain.MOUNTAIN) == ink_group(Terrain.MOUNTAIN_LIGHT)
    assert ink_group(Terrain.WATER) == ink_group(Terrain.BRIDGE_H)
    assert ink_group(Terrain.PLAINS) != ink_group(Terrain.FOREST)
