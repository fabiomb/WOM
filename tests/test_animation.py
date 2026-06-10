"""Tests de la animación de movimiento (interpolación pura, sin display)."""

import random

from wom.core.config import load_game_config, load_unit_classes
from wom.core.game import Game, Player
from wom.core.orders import MoveOrder
from wom.core.victory import VictoryMode
from wom.core.worldmap import Fort, Terrain, WorldMap
from wom.ui.animation import (
    TILES_PER_SECOND,
    ArmyMotion,
    TurnAnimation,
    build_turn_animation,
)


def _motion(waypoints) -> ArmyMotion:
    return ArmyMotion(army_id=0, owner=0, class_id="soldado", troops=10,
                      waypoints=waypoints)


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
