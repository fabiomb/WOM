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


# --- guarnición: la AI no abandona sus fuertes de frente -------------------


def test_guarnece_fuerte_de_frente():
    """Un ejército sobre su fuerte, con el enemigo lejos pero acercándose,
    se queda a guarnecer en vez de salir a por el fuerte rival."""
    game = _make_game(width=20)
    game.spawn_army(0, (0, 2), {"soldado": 50})  # sobre mi propio fuerte
    game.spawn_army(1, (10, 2), {"soldado": 50})  # de frente (dist 10 <= 12)
    army = game.army_at((0, 2))
    orders = AIPlayer(0, "dificil").decide_orders(game)
    order = _move_order_for(orders, army.id)
    # Mantiene la posición: sin orden de mover o con path que no abandona el fuerte.
    assert order is None or not order.path or order.path[-1] == (0, 2)


def test_guarnicion_atrae_ejercito_a_fuerte_indefenso():
    game = _make_game(width=20)
    army = game.spawn_army(0, (4, 2), {"soldado": 30})  # libre, cerca del fuerte
    game.spawn_army(1, (11, 2), {"soldado": 200})  # demasiado fuerte para atacar
    orders = AIPlayer(0, "dificil").decide_orders(game)
    order = _move_order_for(orders, army.id)
    assert order is not None and order.path[-1] == (0, 2)  # acude a guarnecer


# --- fusión y división -----------------------------------------------------


def test_fusiona_ejercitos_dispersos():
    """Dos ejércitos propios pequeños y aledaños, sin enemigos cerca, se
    consolidan en uno solo."""
    from wom.core.orders import MergeArmyOrder

    game = _make_game(width=20)
    a1 = game.spawn_army(0, (4, 2), {"soldado": 20})
    a2 = game.spawn_army(0, (4, 3), {"soldado": 20})
    orders = AIPlayer(0, "dificil").decide_orders(game)
    merges = [o for o in orders if isinstance(o, MergeArmyOrder)]
    assert len(merges) == 1
    assert {merges[0].source_id, merges[0].target_id} == {a1.id, a2.id}


def test_no_fusiona_con_enemigo_cerca():
    """Con un enemigo a tiro, los ejércitos se coordinan para atacar en vez
    de gastar el turno fusionándose."""
    from wom.core.orders import MergeArmyOrder

    game = _make_game(width=20)
    game.spawn_army(0, (8, 2), {"soldado": 20})
    game.spawn_army(0, (8, 3), {"soldado": 20})
    game.spawn_army(1, (10, 2), {"soldado": 30})
    orders = AIPlayer(0, "dificil").decide_orders(game)
    assert not [o for o in orders if isinstance(o, MergeArmyOrder)]


def test_facil_no_fusiona():
    from wom.core.orders import MergeArmyOrder

    game = _make_game(width=20)
    game.spawn_army(0, (4, 2), {"soldado": 20})
    game.spawn_army(0, (4, 3), {"soldado": 20})
    orders = AIPlayer(0, "facil").decide_orders(game)
    assert not [o for o in orders if isinstance(o, MergeArmyOrder)]


def test_divide_para_guarnecer_fuerte_de_frente():
    """Un ejército grande parado en un fuerte de frente desprende un
    destacamento y deja guarnición."""
    from wom.core.orders import SplitArmyOrder

    game = _make_game(width=20)
    big = game.spawn_army(0, (0, 2), {"soldado": 90})  # >= umbral_division (70)
    game.spawn_army(1, (10, 2), {"soldado": 40})  # frente
    orders = AIPlayer(0, "dificil").decide_orders(game)
    splits = [o for o in orders if isinstance(o, SplitArmyOrder)]
    assert len(splits) == 1 and splits[0].source_id == big.id
    moved = sum(n for _, n in splits[0].composition)
    assert 0 < moved < big.total_troops


def test_medio_no_divide():
    from wom.core.orders import SplitArmyOrder

    game = _make_game(width=20)
    game.spawn_army(0, (0, 2), {"soldado": 90})
    game.spawn_army(1, (10, 2), {"soldado": 40})
    orders = AIPlayer(0, "medio").decide_orders(game)
    assert not [o for o in orders if isinstance(o, SplitArmyOrder)]


def test_split_order_aplicada_crea_ejercito():
    """La SplitArmyOrder emitida por la AI se aplica en el motor de turnos."""
    game = _make_game(width=20)
    game.spawn_army(0, (0, 2), {"soldado": 90})
    game.spawn_army(1, (10, 2), {"soldado": 40})
    n_before = len(game.armies_of(0))
    orders = AIPlayer(0, "dificil").decide_orders(game)
    game.run_turn(orders)
    assert len(game.armies_of(0)) == n_before + 1


# --- personalidades --------------------------------------------------------


def test_personalidad_determinista():
    from wom.ai.ai_player import choose_personality

    names = ["agresivo", "estratega", "moderado"]
    a = choose_personality(123, 0, names)
    b = choose_personality(123, 0, names)
    assert a == b and a in names


def test_personalidad_modula_pesos():
    """El agresivo ataca con menos margen y guarnece menos que el estratega."""
    agresivo = AIPlayer(0, "medio", personality="agresivo")
    estratega = AIPlayer(0, "medio", personality="estratega")
    assert agresivo.params["agresividad"] > estratega.params["agresividad"]
    assert agresivo.params["umbral_ataque"] < estratega.params["umbral_ataque"]
    assert agresivo.params["peso_guarnicion"] < estratega.params["peso_guarnicion"]


def test_personalidad_moderado_neutral():
    """La personalidad moderada no cambia los pesos base del nivel."""
    base = AIPlayer(0, "medio")
    moderado = AIPlayer(0, "medio", personality="moderado")
    for key in ("agresividad", "peso_economia", "peso_defensa", "umbral_ataque"):
        assert base.params[key] == moderado.params[key]


def test_personalidad_habilita_capacidad():
    """El estratega habilita `divide` incluso en un nivel que no lo trae."""
    estratega = AIPlayer(0, "medio", personality="estratega")
    assert estratega.params["divide"] is True  # medio base no divide