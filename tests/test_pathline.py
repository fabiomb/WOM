"""Tests del trazado suave de rutas (pathline), puro, sin pygame."""

from math import hypot

from wom.ui.pathline import arrow_head, smooth_path, trim_tail


def test_suavizado_conserva_los_extremos():
    points = [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0)]
    curve = smooth_path(points)
    assert curve[0] == (0.0, 0.0)
    assert curve[-1] == (10.0, 10.0)
    assert len(curve) > len(points)  # cada ronda agrega puntos intermedios


def test_suavizado_redondea_la_esquina():
    points = [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0)]
    curve = smooth_path(points)
    # El vértice duro (10, 0) desaparece: ningún punto de la curva pasa por él.
    assert all(hypot(x - 10, y - 0) > 1.0 for x, y in curve)


def test_suavizado_con_menos_de_tres_puntos_no_cambia():
    segment = [(0.0, 0.0), (5.0, 5.0)]
    assert smooth_path(segment) == segment
    assert smooth_path([(1.0, 2.0)]) == [(1.0, 2.0)]


def test_flecha_apunta_en_la_direccion_del_trazo():
    head = arrow_head([(0.0, 0.0), (10.0, 0.0)], size=4.0)
    tip, left, right = head
    assert tip == (10.0, 0.0)
    assert left[0] == right[0] == 6.0  # la base queda size atrás de la punta
    assert left[1] == -right[1] != 0  # alas simétricas


def test_flecha_sin_direccion_devuelve_vacio():
    assert arrow_head([(5.0, 5.0)], size=4.0) == []
    assert arrow_head([(5.0, 5.0), (5.0, 5.0)], size=4.0) == []


def test_recorte_del_final():
    trimmed = trim_tail([(0.0, 0.0), (10.0, 0.0)], 4.0)
    assert trimmed[-1] == (6.0, 0.0)
    # Recorte más largo que la polilínea: queda solo el inicio.
    assert trim_tail([(0.0, 0.0), (2.0, 0.0)], 10.0) == [(0.0, 0.0)]
