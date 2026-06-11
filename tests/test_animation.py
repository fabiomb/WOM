"""Tests de la animación de movimiento (interpolación pura, sin display)."""

import random

import pytest

from wom.core.config import load_game_config, load_unit_classes
from wom.core.game import Game, Player
from wom.core.orders import MoveOrder
from wom.core.victory import VictoryMode
from wom.core.worldmap import Fort, Terrain, WorldMap
from wom.ui.animation import (
    CLASH_AMPLITUDE,
    CLASH_PULSES,
    CLASH_SECONDS,
    SPAWN_HIGHLIGHT_SECONDS,
    SPAWN_RING_COUNT,
    SPAWN_RING_MAX_RADIUS,
    SPAWN_RING_MIN_RADIUS,
    TILES_PER_SECOND,
    ArmyMotion,
    TurnAnimation,
    build_turn_animation,
    spawn_highlight_rings,
)


def _motion(waypoints, army_id=0, alive=True) -> ArmyMotion:
    return ArmyMotion(army_id=army_id, owner=0, class_id="soldado", troops=10,
                      waypoints=waypoints, alive=alive)


def test_interpolacion_entre_tiles():
    m = _motion([(0, 0), (1, 0), (1, 1)])
    assert m.position_at(0.0) == (0.0, 0.0)
    half_tile = 0.5 / TILES_PER_SECOND
    assert m.position_at(half_tile) == (0.5, 0.0)
    assert m.position_at(m.duration) == (1.0, 1.0)
    assert m.position_at(999.0) == (1.0, 1.0)  # nunca pasa el final
    assert m.duration == 2 / TILES_PER_SECOND


def test_ejercito_quieto_no_anima():
    m = _motion([(3, 4)])
    assert m.duration == 0.0
    assert m.position_at(0.0) == (3.0, 4.0)


def test_turn_animation_termina_y_se_saltea():
    anim = TurnAnimation([_motion([(0, 0), (1, 0)]), _motion([(5, 5)])])
    assert anim.duration == 1 / TILES_PER_SECOND  # la del recorrido más largo
    assert not anim.finished(0.0)
    assert anim.finished(anim.duration)
    anim.skip()
    assert anim.finished(0.0)


def test_fases_de_choque_y_retirada():
    attacker = _motion([(1, 1), (2, 1), (3, 1)], army_id=0)
    defender = _motion([(4, 1), (5, 1)], army_id=1)  # su único paso: la retirada
    anim = TurnAnimation(
        [attacker, defender], clashes=[(0, 1)], retreated_ids=frozenset({1})
    )
    march = 2 / TILES_PER_SECOND
    assert anim.move_duration == march
    assert anim.duration == march + CLASH_SECONDS + 1 / TILES_PER_SECOND

    # durante la marcha el defensor espera quieto (la retirada va al final)
    by_id = dict((m.army_id, p) for m, p in anim.positions(march / 2))
    assert by_id[1] == (4.0, 1.0)

    # pico de la primera embestida: ambos avanzan hacia el rival
    peak = march + CLASH_SECONDS / (CLASH_PULSES * 2)
    by_id = dict((m.army_id, p) for m, p in anim.positions(peak))
    assert by_id[0][0] == pytest.approx(3 + CLASH_AMPLITUDE)
    assert by_id[1][0] == pytest.approx(4 - CLASH_AMPLITUDE)
    effects = anim.clash_effects(peak)
    assert effects == [((3.5, 1.0), pytest.approx(1.0))]
    assert anim.clash_effects(march / 2) == []  # fuera de la fase: sin destello

    # fase de retirada: el defensor recién ahora recorre su paso
    halfway = march + CLASH_SECONDS + 0.5 / TILES_PER_SECOND
    by_id = dict((m.army_id, p) for m, p in anim.positions(halfway))
    assert by_id[1] == pytest.approx((4.5, 1.0))
    assert by_id[0] == (3.0, 1.0)  # el atacante ya no embiste
    assert not anim.finished(halfway)
    assert anim.finished(anim.duration)


def test_el_resultado_se_revela_al_terminar_el_choque():
    attacker = _motion([(2, 1), (3, 1)], army_id=0)  # troops finales: 10
    defender = _motion([(4, 1)], army_id=1)
    anim = TurnAnimation(
        [attacker, defender], clashes=[(0, 1)],
        troops_before={0: 100, 1: 40},
    )
    # marcha y choque: se ven las tropas previas a la batalla
    in_clash = anim.move_duration + CLASH_SECONDS / 2
    assert not anim.result_revealed(in_clash)
    troops = {m.army_id: m.troops for m, _ in anim.positions(in_clash)}
    assert troops == {0: 100, 1: 40}
    # terminado el choque se revela el conteo final
    after = anim.move_duration + CLASH_SECONDS
    assert anim.result_revealed(after)
    troops = {m.army_id: m.troops for m, _ in anim.positions(after)}
    assert troops == {0: 10, 1: 10}


def test_el_muerto_desaparece_despues_del_choque():
    winner = _motion([(2, 1), (3, 1)], army_id=0)
    dead = _motion([(4, 1)], army_id=1, alive=False)
    anim = TurnAnimation([winner, dead], clashes=[(0, 1)])
    in_clash = anim.move_duration + CLASH_SECONDS / 2
    assert {m.army_id for m, _ in anim.positions(in_clash)} == {0, 1}
    after = anim.move_duration + CLASH_SECONDS
    assert {m.army_id for m, _ in anim.positions(after)} == {0}


def test_spawn_highlight_rings():
    assert spawn_highlight_rings(-0.1) == []
    assert spawn_highlight_rings(SPAWN_HIGHLIGHT_SECONDS) == []
    rings = spawn_highlight_rings(0.0)
    assert len(rings) == SPAWN_RING_COUNT
    for radius, opacity in rings:
        assert SPAWN_RING_MIN_RADIUS <= radius <= SPAWN_RING_MAX_RADIUS
        assert 0.0 < opacity <= 1.0
    # apagado suave: cerca del final la opacidad queda acotada por el fade
    closing = spawn_highlight_rings(SPAWN_HIGHLIGHT_SECONDS - 0.25)
    assert all(opacity <= 0.25 for _, opacity in closing)


def _make_game() -> Game:
    tiles = [[Terrain.PLAINS] * 8 for _ in range(5)]
    world = WorldMap(width=8, height=5, tiles=tiles)
    world.forts.append(Fort(position=(0, 2), owner=0))
    world.forts.append(Fort(position=(7, 2), owner=1))
    return Game(
        world=world,
        players=[Player(0, "P0"), Player(1, "P1")],
        armies=[],
        classes=load_unit_classes(),
        config=load_game_config(),
        victory_mode=VictoryMode.TOTAL,
        rng=random.Random(1),
        seed=1,
    )


def test_build_turn_animation_desde_last_moves():
    game = _make_game()
    mover = game.spawn_army(0, (1, 1), {"soldado": 10})
    still = game.spawn_army(1, (6, 3), {"arquero": 5})
    pre_turn = [a.to_dict() for a in game.armies]
    game.run_turn([MoveOrder(army_id=mover.id, path=((2, 1), (3, 1)))])

    anim = build_turn_animation(game, pre_turn)
    assert anim is not None
    by_id = {m.army_id: m for m in anim.motions}
    assert by_id[mover.id].waypoints == [(1, 1), (2, 1), (3, 1)]
    assert by_id[mover.id].alive
    assert by_id[still.id].waypoints == [(6, 3)]  # quieto: un solo waypoint


def test_build_turn_animation_sin_movimiento_devuelve_none():
    game = _make_game()
    game.spawn_army(0, (1, 1), {"soldado": 10})
    pre_turn = [a.to_dict() for a in game.armies]
    game.run_turn([])
    assert build_turn_animation(game, pre_turn) is None


def test_build_turn_animation_con_batalla_sin_movimiento():
    game = _make_game()
    a = game.spawn_army(0, (2, 1), {"soldado": 100})
    game.spawn_army(1, (3, 1), {"soldado": 100})  # ya adyacente
    pre_turn = [army.to_dict() for army in game.armies]
    game.run_turn([MoveOrder(army_id=a.id, path=((3, 1),))])
    assert game.last_clashes  # hubo batalla aunque nadie pisó tiles nuevos

    anim = build_turn_animation(game, pre_turn)
    assert anim is not None  # el choque es el feedback de la batalla
    assert anim.clash_points
    assert anim.clash_duration == CLASH_SECONDS


def test_build_muestra_tropas_previas_durante_el_choque():
    game = _make_game()
    strong = game.spawn_army(0, (1, 1), {"soldado": 100})
    weak = game.spawn_army(1, (3, 1), {"soldado": 30})
    pre_turn = [a.to_dict() for a in game.armies]
    game.run_turn([MoveOrder(army_id=strong.id, path=((2, 1), (3, 1)))])

    anim = build_turn_animation(game, pre_turn)
    in_clash = anim.move_duration + CLASH_SECONDS / 2
    troops = {m.army_id: m.troops for m, _ in anim.positions(in_clash)}
    # toda batalla causa bajas, pero durante el choque se ve el conteo previo
    assert troops[strong.id] == 100 and troops[weak.id] == 30
    revealed = {
        m.army_id: m.troops for m, _ in anim.positions(anim.duration)
    }
    assert revealed[strong.id] == strong.total_troops < 100


def test_build_turn_animation_conserva_a_los_muertos():
    game = _make_game()
    strong = game.spawn_army(0, (1, 1), {"soldado": 100})
    weak = game.spawn_army(1, (3, 1), {"partisano": 1})  # muere seguro
    pre_turn = [a.to_dict() for a in game.armies]
    game.run_turn([MoveOrder(army_id=strong.id, path=((2, 1), (3, 1)))])
    assert game.army_by_id(weak.id) is None  # efectivamente murió

    anim = build_turn_animation(game, pre_turn)
    by_id = {m.army_id: m for m in anim.motions}
    assert not by_id[weak.id].alive
    assert by_id[weak.id].troops == 1  # con su composición pre-turno
