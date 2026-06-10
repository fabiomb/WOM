"""Test de integración: partida AI vs AI completa, headless y determinista."""

from wom.core.mapgen import MapParams
from wom.headless import run_headless


def test_partida_completa_termina():
    result = run_headless(seed=2026, quiet=True)
    assert result.is_over  # el modo TIME garantiza fin en el turno límite


def test_partida_determinista():
    a = run_headless(seed=555, quiet=True)
    b = run_headless(seed=555, quiet=True)
    assert a == b


def test_mapa_chico_tambien_funciona():
    params = MapParams(width=12, height=10, n_forts=2, n_towns=2, seed=9)
    result = run_headless(map_params=params, quiet=True)
    assert result.is_over
