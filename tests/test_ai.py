"""Tests de comportamiento de la AI: los pesos del nivel cambian la decisión."""

import random

from wom.ai.ai_player import AIPlayer
from wom.core.config import load_game_config, load_unit_classes
from wom.core.game import Game, Player
from wom.core.orders import MoveOrder
from wom.core.victory import VictoryMode
from wom.core.worldmap import Fort, Terrain, WorldMap


def _make_game(width=20, height=5) -> Game:
    """Mapa plano: fuerte de P0 en (0,2), fuerte de P1 en (width-1,2)."""
    tiles = [[Terrain.PLAINS] * width for _ in range(height)]
    world = WorldMap(width=width, height=height, tiles=tiles)
    world.forts.append(Fort(position=(0, 2), owner=0))
    world.forts.append(Fort(position=(width - 1, 2), owner=1))
    return Game(
        world=world,
        players=[Player(0, "AI", is_ai=True), Player(1, "Rival")],
        armies=[],
        classes=load_unit_classes(),
        config=load_game_config(),
        victory_mode=VictoryMode.TOTAL,
        rng=random.Random(1),
        seed=1,
    )


def _move_order_for(orders, army_id) -> MoveOrder | None:
    return next(
        (o for o in orders if isinstance(o, MoveOrder) and o.army_id == army_id), None
    )


def test_medio_defiende_fuerte_amenazado():
    game = _make_game()
    army = game.spawn_army(0, (4, 2), {"soldado": 60})
    game.spawn_army(1, (3, 2), {"soldado": 60})  # amenaza cerca de mi fuerte
    orders = AIPlayer(0, "medio").decide_orders(game)
    order = _move_order_for(orders, army.id)
    assert order is not None and order.path[-1] == (0, 2)  # corre a defender


def test_medio_ataca_con_ventaja():
    game = _make_game()
    army = game.spawn_army(0, (10, 2), {"soldado": 60})
    weak = game.spawn_army(1, (12, 2), {"soldado": 20})  # lejos de mi fuerte
    orders = AIPlayer(0, "medio").decide_orders(game)
    order = _move_order_for(orders, army.id)
    assert order is not None and order.path[-1] == weak.position


def test_medio_no_ataca_en_desventaja():
    game = _make_game()
    army = game.spawn_army(0, (10, 2), {"soldado": 30})
    strong = game.spawn_army(1, (12, 2), {"soldado": 100})
    orders = AIPlayer(0, "medio").decide_orders(game)
    order = _move_order_for(orders, army.id)
    assert order is not None and order.path[-1] != strong.position


def test_dificil_reabastece_ejercito_daniado():
    game = _make_game()
    game.world.forts[0].reserve = {"soldado": 50}
    army = game.spawn_army(0, (5, 2), {"soldado": 20})  # dañado (< 50% de 100)
    orders = AIPlayer(0, "dificil").decide_orders(game)
    order = _move_order_for(orders, army.id)
    assert order is not None and order.path[-1] == (0, 2)  # vuelve al fuerte


def test_facil_no_reabastece_prefiere_capturar():
    game = _make_game()
    game.world.forts[0].reserve = {"soldado": 50}
    army = game.spawn_army(0, (5, 2), {"soldado": 20})
    orders = AIPlayer(0, "facil").decide_orders(game)
    order = _move_order_for(orders, army.id)
    assert order is not None and order.path[-1] == (19, 2)  # va por el fuerte rival


def test_dificil_agrupa_para_atacar():
    game = _make_game()
    a1 = game.spawn_army(0, (8, 2), {"soldado": 40})
    a2 = game.spawn_army(0, (8, 3), {"soldado": 40})
    enemy = game.spawn_army(1, (11, 2), {"soldado": 50})
    orders = AIPlayer(0, "dificil").decide_orders(game)
    # individualmente 40 vs 50 no alcanza el umbral 1.3, pero combinados sí:
    # ambos ejércitos convergen sobre el mismo enemigo
    for army in (a1, a2):
        order = _move_order_for(orders, army.id)
        assert order is not None and order.path[-1] == enemy.position


def test_medio_no_agrupa():
    game = _make_game()
    a1 = game.spawn_army(0, (8, 2), {"soldado": 40})
    a2 = game.spawn_army(0, (8, 3), {"soldado": 40})
    enemy = game.spawn_army(1, (11, 2), {"soldado": 50})
    orders = AIPlayer(0, "medio").decide_orders(game)
    for army in (a1, a2):
        order = _move_order_for(orders, army.id)
        assert order is None or order.path[-1] != enemy.position


def test_decisiones_deterministas():
    def build():
        game = _make_game()
        game.spawn_army(0, (4, 2), {"soldado": 60, "arquero": 10})
        game.spawn_army(1, (9, 2), {"soldado": 50})
        return game

    orders_a = AIPlayer(0, "dificil").decide_orders(build())
    orders_b = AIPlayer(0, "dificil").decide_orders(build())
    assert orders_a == orders_b