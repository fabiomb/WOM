"""Tests de la cámara del mapa (zoom + paneo), pura, sin pygame."""

from wom.ui.camera import EDGE_MARGIN, Camera

AREA = (0, 0, 1440, 1080)


def _camera() -> Camera:
    # Mapa "medio" (30x20) con tile base 48: 1440x960 px, entra justo a lo
    # ancho y centrado a lo alto.
    return Camera(AREA, (30, 20), 48)


def test_nivel_cero_encuadra_el_mapa_centrado():
    cam = _camera()
    assert cam.tile_size == 48
    assert cam.origin == (0, 60)  # (1080 - 960) / 2
    assert not cam.zoomed


def test_zoom_in_mantiene_el_punto_bajo_el_cursor():
    cam = _camera()
    anchor = (720, 540)  # centro del área: lejos de los bordes, sin clamp
    ox, oy = cam.origin
    tile_x = (anchor[0] - ox) / cam.tile_size
    tile_y = (anchor[1] - oy) / cam.tile_size
    assert cam.zoom(1, anchor)
    nx, ny = cam.origin
    assert abs((anchor[0] - nx) / cam.tile_size - tile_x) < 0.05
    assert abs((anchor[1] - ny) / cam.tile_size - tile_y) < 0.05
    assert cam.zoomed and cam.tile_size > 48


def test_zoom_no_pasa_de_los_extremos():
    cam = _camera()
    assert not cam.zoom(-1, (0, 0))  # ya está en el encuadre completo
    for _ in range(20):
        cam.zoom(1, (0, 0))
    top = cam.tile_size
    assert not cam.zoom(1, (0, 0))  # tope alcanzado
    assert cam.tile_size == top <= 128


def test_zoom_out_vuelve_al_encuadre_centrado():
    cam = _camera()
    cam.zoom(1, (200, 200))
    cam.pan(300, 300)
    cam.zoom(-1, (700, 500))
    assert cam.origin == (0, 60)  # nivel 0: siempre centrado


def test_el_paneo_se_clampa_a_los_bordes_del_mapa():
    cam = _camera()
    cam.zoom(1, (720, 540))
    cam.pan(-100000, -100000)  # vista hacia la esquina superior izquierda
    assert cam.origin == (0, 0)
    cam.pan(100000, 100000)
    ox, oy = cam.origin
    assert ox == AREA[2] - 30 * cam.tile_size
    assert oy == AREA[3] - 20 * cam.tile_size


def test_edge_pan_solo_mueve_con_zoom():
    cam = _camera()
    assert not cam.edge_pan((1440 - 1, 540), 0.1)  # sin zoom: nada que panear
    cam.zoom(1, (720, 540))
    before = cam.origin
    assert cam.edge_pan((1440 - 1, 540), 0.1)  # borde derecho: va a la derecha
    assert cam.origin[0] < before[0]
    assert cam.origin[1] == before[1]


def test_edge_pan_ignora_el_mouse_fuera_del_area():
    cam = _camera()
    cam.zoom(1, (720, 540))
    before = cam.origin
    assert not cam.edge_pan((1500, 540), 0.1)  # en el HUD: no panea
    assert cam.origin == before


def test_edge_pan_respeta_el_margen():
    cam = _camera()
    cam.zoom(1, (720, 540))
    before = cam.origin
    inside = (AREA[2] // 2, AREA[3] // 2)
    assert EDGE_MARGIN < inside[0] < AREA[2] - EDGE_MARGIN
    assert not cam.edge_pan(inside, 0.1)  # lejos de los bordes: quieto
    assert cam.origin == before
