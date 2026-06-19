"""Smoke tests de la pantalla de combate táctico (driver dummy de SDL)."""

import os
import random

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

import pygame
import pytest

from wom.core.army import Army
from wom.core.config import load_game_config, load_unit_classes
from wom.core.tactical import build_tactical_battle
from wom.core.worldmap import Fort, Terrain, WorldMap
from wom.ui import theme
from wom.ui.battle_screen import HORIZON_FORT, HORIZON_OPEN, BattleScreen


@pytest.fixture(scope="module")
def screen():
    pygame.init()
    yield pygame.display.set_mode(theme.WINDOW_SIZE)
    pygame.quit()


def _battle():
    tiles = [[Terrain.PLAINS] * 10 for _ in range(8)]
    world = WorldMap(width=10, height=8, tiles=tiles)
    a = Army(id=0, owner=0, position=(2, 2), composition={"soldado": 30, "arquero": 10})
    d = Army(id=1, owner=1, position=(6, 2), composition={"soldado": 25, "caballero": 10})
    return build_tactical_battle(
        a, d, world, load_unit_classes(), load_game_config()["batalla"], random.Random(4)
    )


def _fort_battle():
    tiles = [[Terrain.PLAINS] * 10 for _ in range(8)]
    world = WorldMap(width=10, height=8, tiles=tiles)
    world.forts.append(Fort(position=(6, 2), owner=1))
    a = Army(id=0, owner=0, position=(5, 2), composition={"soldado": 30})
    d = Army(id=1, owner=1, position=(6, 2), composition={"soldado": 25})
    return build_tactical_battle(
        a, d, world, load_unit_classes(), load_game_config()["batalla"], random.Random(4)
    )


def test_campo_abierto_usa_fondo_y_profundidad(screen):
    bs = BattleScreen(_battle(), human_owner=0)
    assert bs.has_bg  # campo abierto: carga el fondo de pradera
    assert bs._horizon == HORIZON_OPEN
    # Profundidad: las de arriba (lejos) más chicas que las de abajo (cerca).
    assert bs._depth(0) < bs._depth(bs.battle.field_h)
    assert abs(bs._depth(0) - 0.8) < 1e-6
    bs.draw(screen)  # render con fondo sin lanzar


def test_castillo_usa_fondo_de_fuerte(screen):
    bs = BattleScreen(_fort_battle(), human_owner=0)
    assert bs.has_bg                     # el fuerte tiene su propio fondo
    assert bs._horizon == HORIZON_FORT   # terreno arranca ~1/3 (cielo arriba)
    assert bs.battle.walls and bs.battle.gate_cells  # muralla+puerta (colisión)
    bs.draw(screen)  # render con fondo de fuerte sin lanzar


def test_fuerte_inclina_la_grilla_y_to_cell_invierte_to_px(screen):
    bs = BattleScreen(_fort_battle(), human_owner=0)
    assert bs._shear > 0  # el fuerte inclina la grilla (perspectiva del patio)
    # Los clics siguen siendo correctos: to_cell deshace el shear de to_px.
    for cx, cy in [(3.0, 2.0), (8.0, 6.0), (5.5, 5.5)]:
        px, py = bs.to_px(cx, cy)
        rx, ry = bs.to_cell(px, py)
        assert abs(rx - cx) < 0.1 and abs(ry - cy) < 0.1
    # El campo abierto no se inclina.
    assert BattleScreen(_battle(), human_owner=0)._shear == 0.0


def test_render_y_pasos(screen):
    bs = BattleScreen(_battle(), human_owner=0)
    bs.update()  # primer frame: fija el reloj
    for _ in range(5):
        bs.update()
        bs.draw(screen)  # no debe lanzar


def test_arranca_en_preparacion_con_tropas_en_aguantar(screen):
    bs = BattleScreen(_battle(), human_owner=0)
    assert bs.phase == "planning"
    # Las fichas del humano arrancan aguantando (no atacan hasta que se ordena).
    mine = [u for u in bs.battle.units if u.owner == 0]
    assert mine and all(u.order == ("hold",) for u in mine)
    # En preparación, update no avanza la simulación.
    bs.update()
    t0 = bs.battle.elapsed
    for _ in range(5):
        bs.update()
    assert bs.battle.elapsed == t0 == 0.0
    # Espacio arranca el combate.
    bs.handle_event(pygame.event.Event(pygame.KEYDOWN, {"key": pygame.K_SPACE}))
    assert bs.phase == "fighting"


def test_contador_arranca_el_combate_al_llegar_a_cero(screen):
    bs = BattleScreen(_battle(), human_owner=0)
    assert bs.phase == "planning"
    bs._countdown = 0.05
    bs.update()  # fija el reloj base
    bs._last_ticks -= 1000  # simula que pasó ~1s
    bs.update()  # el contador llega a 0 → empieza el combate
    assert bs.phase == "fighting"


def test_clic_simple_selecciona_una_unidad(screen):
    bs = BattleScreen(_battle(), human_owner=0)
    unit = next(u for u in bs.battle.units if u.owner == 0)
    pos = bs.to_px(unit.x, unit.y)
    bs.handle_event(pygame.event.Event(pygame.MOUSEBUTTONDOWN, {"pos": pos, "button": 1}))
    bs.handle_event(pygame.event.Event(pygame.MOUSEBUTTONUP, {"pos": pos, "button": 1}))
    assert bs.selected == {unit.id}


def test_boton_comenzar_arranca_el_combate(screen):
    bs = BattleScreen(_battle(), human_owner=0)
    bs.handle_event(
        pygame.event.Event(
            pygame.MOUSEBUTTONDOWN, {"pos": bs._start_btn.center, "button": 1}
        )
    )
    assert bs.phase == "fighting"


def test_los_arqueros_generan_flechas(screen):
    bs = BattleScreen(_battle(), human_owner=0)
    bs.phase = "fighting"
    bs.update()  # fija el reloj
    # Avanza el combate hasta que algún arquero dispare (genera flechas).
    saw_arrow = False
    for _ in range(600):
        bs.ai.update(bs.battle, 1 / 30)
        bs.battle.step(1 / 30)
        bs._spawn_arrows()
        bs._age_arrows(1 / 30)
        if bs._arrows:
            saw_arrow = True
            break
        if bs.battle.finished:
            break
    assert saw_arrow
    bs.draw(screen)  # dibuja flechas sin lanzar


def test_seleccion_y_orden(screen):
    bs = BattleScreen(_battle(), human_owner=0)
    # Selecciona todas mis fichas con A y les da orden de aguantar con H.
    bs.handle_event(pygame.event.Event(pygame.KEYDOWN, {"key": pygame.K_a}))
    assert bs.selected
    bs.handle_event(pygame.event.Event(pygame.KEYDOWN, {"key": pygame.K_h}))
    assert all(
        bs.battle.unit_by_id(uid).order == ("hold",) for uid in bs.selected
    )


def test_boton_auto_resolver(screen):
    bs = BattleScreen(_battle(), human_owner=0)
    bs.handle_event(
        pygame.event.Event(
            pygame.MOUSEBUTTONDOWN, {"pos": bs._auto_btn.center, "button": 1}
        )
    )
    assert bs.auto_resolve and bs.done


def test_batalla_corre_hasta_terminar_y_da_resultado(screen):
    bs = BattleScreen(_battle(), human_owner=0)
    bs.update()
    # Simula muchos frames con dt fijo conduciendo el modelo directamente.
    for _ in range(20000):
        bs.ai.update(bs.battle, 1 / 30)
        bs.battle.step(1 / 30)
        if bs.battle.finished:
            break
    bs.result = bs.battle.to_battle_result()
    bs.draw(screen)  # dibuja el cartel de resultado sin lanzar
    assert bs.result is not None
    # Un clic cierra el resumen.
    bs.handle_event(pygame.event.Event(pygame.MOUSEBUTTONDOWN, {"pos": (10, 10), "button": 1}))
    assert bs.done
