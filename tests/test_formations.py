"""Tests de las formaciones de combate (wom/core/formations.py, puro)."""

from statistics import mean

import pytest

from wom.core import formations
from wom.core.config import load_unit_classes
from wom.core.tactical import Unit


def _classes():
    return load_unit_classes()


def _units(spec: dict[str, int]) -> list[Unit]:
    """Una ficha por cada `count` de tropas de la clase indicada."""
    units, uid = [], 0
    for class_id, n_tokens in spec.items():
        for _ in range(n_tokens):
            units.append(
                Unit(id=uid, owner=0, class_id=class_id, troops0=5,
                     hp=5.0, max_hp=5.0, x=0.0, y=0.0)
            )
            uid += 1
    return units


ZONE = (2.0, 8.0, 1.0, 11.0)  # x0,x1,y0,y1


def _arrange(units, formation, facing=1):
    formations.arrange(units, ZONE, facing=facing, formation=formation, classes=_classes())


@pytest.mark.parametrize("formation", formations.FORMATIONS)
def test_todas_las_fichas_quedan_dentro_de_la_zona(formation):
    units = _units({"soldado": 8, "arquero": 4, "caballero": 4})
    _arrange(units, formation)
    x0, x1, y0, y1 = ZONE
    for u in units:
        assert x0 - 1e-6 <= u.x <= x1 + 1e-6
        assert y0 - 1e-6 <= u.y <= y1 + 1e-6


def test_linea_es_ancha_y_poco_profunda():
    units = _units({"soldado": 12})
    _arrange(units, formations.LINE)
    spread_y = max(u.y for u in units) - min(u.y for u in units)
    spread_x = max(u.x for u in units) - min(u.x for u in units)
    assert spread_y > spread_x  # se despliega a lo ancho, no en profundidad


def test_clasica_pone_arqueros_detras_de_la_infanteria():
    units = _units({"soldado": 8, "arquero": 6, "caballero": 4})
    _arrange(units, formations.CLASSIC, facing=1)  # atacante mira a +x
    inf_x = mean(u.x for u in units if u.class_id == "soldado")
    arc_x = mean(u.x for u in units if u.class_id == "arquero")
    cav_y = [u.y for u in units if u.class_id == "caballero"]
    # arqueros más atrás (menor x) que la infantería que va al frente
    assert arc_x < inf_x
    # caballería en los flancos: hay tanto arriba como abajo del centro
    centro = (ZONE[2] + ZONE[3]) / 2
    assert min(cav_y) < centro < max(cav_y)


def test_compacta_es_mas_apretada_que_la_linea():
    spec = {"soldado": 12, "arquero": 6}
    linea = _units(spec)
    compacta = _units(spec)
    _arrange(linea, formations.LINE)
    _arrange(compacta, formations.COMPACT)
    ancho_linea = max(u.y for u in linea) - min(u.y for u in linea)
    ancho_compacta = max(u.y for u in compacta) - min(u.y for u in compacta)
    assert ancho_compacta < ancho_linea


def test_v_tiene_la_punta_al_frente_y_centrada():
    units = _units({"soldado": 9})
    _arrange(units, formations.VEE, facing=1)
    frente = max(units, key=lambda u: u.x)  # la punta (más cerca del enemigo)
    centro_y = (ZONE[2] + ZONE[3]) / 2
    bordes = max(centro_y - ZONE[2], ZONE[3] - centro_y)
    assert abs(frente.y - centro_y) < bordes * 0.5  # la punta va cerca del centro


def test_recommend_usa_linea_para_ejercitos_chicos():
    assert formations.recommend(_units({"soldado": 1}), _classes()) == formations.LINE


def test_recommend_clasica_con_armas_combinadas():
    units = _units({"soldado": 6, "arquero": 4, "caballero": 2})
    assert formations.recommend(units, _classes()) == formations.CLASSIC


def test_recommend_v_con_caballeria_e_infanteria_sin_arqueros():
    units = _units({"soldado": 6, "caballero": 4})
    assert formations.recommend(units, _classes()) == formations.VEE


def test_recommend_compacta_solo_infanteria():
    units = _units({"soldado": 10})
    assert formations.recommend(units, _classes()) == formations.COMPACT
