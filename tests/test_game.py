"""Tests del motor de turnos sobre escenarios construidos a mano."""

import random

import pytest

from wom.core.army import Army
from wom.core.config import load_game_config, load_unit_classes
from wom.core.game import Game, Player
from wom.core.orders import CreateArmyOrder, MoveOrder
from wom.core.victory import VictoryMode
from wom.core.worldmap import Fort, Terrain, Town, WorldMap


def _make_game(width=8, height=5, victory_mode=VictoryMode.TOTAL) -> Game:
    """Partida mínima sobre un mapa plano construido a mano (sin mapgen)."""
    tiles = [[Terrain.PLAINS] * width for _ in range(height)]
    world = WorldMap(width=width, height=height, tiles=tiles)
    world.forts.append(Fort(position=(0, 2), owner=0))
    world.forts.append(Fort(position=(width - 1, 2), owner=1))
    return Game(
        world=world,
        players=[Player(0, "P0"), Player(1, "P1")],
        armies=[],
        classes=load_unit_classes(),
        config=load_game_config(),
        victory_mode=victory_mode,
        rng=random.Random(42),
        seed=42,
    )


def test_movimiento_consume_velocidad():
    game = _make_game()
    army = game.spawn_army(0, (1, 1), {"soldado": 10})  # velocidad soldado: 3
    path = [(2, 1), (3, 1), (4, 1), (5, 1), (6, 1)]
    game.run_turn([MoveOrder(army_id=army.id, path=tuple(path))])
    assert army.position == (4, 1)  # 3 tiles de llanura con velocidad 3
    assert army.path == [(5, 1), (6, 1)]  # el resto queda para el próximo turno


def test_last_moves_registra_el_recorrido():
    game = _make_game()
    army = game.spawn_army(0, (1, 1), {"soldado": 10})  # velocidad 3
    game.run_turn([MoveOrder(army_id=army.id, path=((2, 1), (3, 1), (4, 1)))])
    assert game.last_moves[army.id] == [(1, 1), (2, 1), (3, 1), (4, 1)]
    game.run_turn([])  # sin órdenes: el registro del turno anterior se limpia
    assert game.last_moves == {}


def test_last_moves_incluye_la_retirada():
    game = _make_game()
    a = game.spawn_army(0, (2, 1), {"soldado": 100})
    b = game.spawn_army(1, (4, 1), {"soldado": 5})  # muy inferior: pierde y huye
    game.run_turn([MoveOrder(army_id=a.id, path=((3, 1), (4, 1)))])
    if b in game.armies:  # si sobrevivió, su recorrido es la retirada
        moves = game.last_moves[b.id]
        assert moves[0] == (4, 1) and moves[-1] == b.position != (4, 1)


def test_encuentro_detiene_y_pelea():
    game = _make_game()
    a = game.spawn_army(0, (2, 1), {"soldado": 50})
    b = game.spawn_army(1, (4, 1), {"soldado": 50})
    troops_before = a.total_troops + b.total_troops
    game.run_turn([MoveOrder(army_id=a.id, path=((3, 1), (4, 1)))])
    # hubo batalla: ambos perdieron tropas y XP, y nadie comparte tile
    survivors = [x for x in (a, b) if x in game.armies]
    assert sum(x.total_troops for x in survivors) < troops_before
    positions = [x.position for x in game.armies]
    assert len(positions) == len(set(positions))


def test_captura_de_fuerte_neutral():
    game = _make_game()
    game.world.forts.append(Fort(position=(3, 1)))  # neutral
    army = game.spawn_army(0, (2, 1), {"soldado": 10})
    game.run_turn([MoveOrder(army_id=army.id, path=((3, 1),))])
    assert game.world.fort_at((3, 1)).owner == 0


def test_produccion_acumula_en_reserva_sin_crear_ejercitos():
    game = _make_game()
    game.players[0].food = 10
    fort = game.world.forts[0]
    game.run_turn([])
    # tasa 0.5 sobre 10 de comida => 5 tropas a la reserva, ningún ejército nuevo
    assert fort.reserve_total == 5
    assert game.armies == []
    assert game.players[0].food == 0  # la producción consumió el stock


def test_town_produce_comida():
    game = _make_game()
    game.world.towns.append(Town(position=(2, 2), owner=0))
    game.world.forts[0].reserve = {"soldado": game.config["max_reserva_fort"]}
    game.players[0].food = 10
    game.run_turn([])
    # reserva llena => el fuerte no produce ni consume; el town suma comida
    assert game.players[0].food == 10 + game.config["comida_por_town"]


def test_fuertes_capturados_tambien_producen():
    game = _make_game()
    # P0 tiene un segundo fuerte (capturado); la comida alcanza para uno solo
    second = Fort(position=(3, 1), owner=0)
    game.world.forts.append(second)
    first = game.world.forts[0]
    for _ in range(2):
        game.players[0].food = 2  # da para 1 tropa por turno
        game.run_turn([])
    # el orden rotativo le dio su turno de producción a cada fuerte
    assert first.reserve_total > 0
    assert second.reserve_total > 0


def test_pueblo_propio_alimenta_al_ejercito_sin_stock():
    game = _make_game()
    game.world.towns.append(Town(position=(2, 2), owner=0))
    army = game.spawn_army(0, (2, 2), {"soldado": 10})
    army.food = 10
    game.players[0].food = 0  # sin stock: el pueblo lo alimenta igual
    game.run_turn([])
    assert army.food > 10


def test_contador_de_tropas_perdidas_en_batalla():
    game = _make_game()
    a = game.spawn_army(0, (2, 1), {"soldado": 50})
    b = game.spawn_army(1, (4, 1), {"soldado": 50})
    game.run_turn([MoveOrder(army_id=a.id, path=((3, 1), (4, 1)))])
    p0, p1 = game.players
    assert p0.troops_lost >= 1 and p1.troops_lost >= 1  # toda batalla causa bajas
    # invariante: tropas perdidas + tropas vivas = tropas iniciales
    assert p0.troops_lost + sum(x.total_troops for x in game.armies_of(0)) == 50
    assert p1.troops_lost + sum(x.total_troops for x in game.armies_of(1)) == 50


def test_ejercito_disuelto_cuenta_sus_tropas_como_perdidas():
    game = _make_game()
    army = game.spawn_army(0, (4, 4), {"soldado": 10})
    army.xp = 0  # muere por moral: sus 10 tropas restantes son bajas
    game.run_turn([])
    assert game.players[0].troops_lost == 10


def test_reabastecimiento_en_fuerte_propio():
    game = _make_game()
    fort = game.world.forts[0]
    fort.reserve = {"soldado": 50}
    army = game.spawn_army(0, fort.position, {"soldado": 10})
    game.run_turn([])
    # el ejército estacionado en su fuerte se recarga desde la reserva
    assert army.total_troops == 60
    assert fort.reserve_total == 0


def test_reabastecimiento_respeta_limite_de_ejercito():
    game = _make_game()
    fort = game.world.forts[0]
    fort.reserve = {"soldado": 100}
    army = game.spawn_army(0, fort.position, {"soldado": 80})
    game.run_turn([])
    assert army.total_troops == game.config["max_army_size"]
    assert fort.reserve_total == 100 - (game.config["max_army_size"] - 80)


def test_crear_ejercito_es_accion_voluntaria():
    game = _make_game()
    fort = game.world.forts[0]
    fort.reserve = {"soldado": 30, "arquero": 10}
    game.run_turn([CreateArmyOrder(position=fort.position)])
    army = game.army_at(fort.position)
    assert army is not None and army.owner == 0
    assert army.total_troops >= 40  # tomó toda la reserva (+ producción del turno)


def test_captura_de_fuerte_destruye_la_reserva():
    game = _make_game()
    fort = game.world.forts[0]
    fort.position = (3, 1)  # lo movemos para alcanzarlo fácil
    fort.reserve = {"soldado": 80}
    fort.owner = 1
    army = game.spawn_army(0, (2, 1), {"soldado": 10})
    game.run_turn([MoveOrder(army_id=army.id, path=((3, 1),))])
    assert fort.owner == 0
    assert fort.reserve_total == 0  # la guarnición enemiga se dispersó


def test_recuperacion_xp_en_fuerte_vs_campo():
    game = _make_game()
    in_fort = game.spawn_army(0, (0, 2), {"soldado": 10})
    in_field = game.spawn_army(0, (4, 4), {"soldado": 10})
    in_fort.xp = in_field.xp = 50
    game.run_turn([])
    cfg = game.config["xp_recuperacion"]
    assert in_fort.xp == 50 + cfg["fort"]
    assert in_field.xp == 50 + cfg["campo"]


def test_ejercito_sin_xp_muere_con_cruz():
    game = _make_game()
    army = game.spawn_army(0, (4, 4), {"soldado": 10})
    army.xp = 0
    game.run_turn([])
    assert army not in game.armies
    assert (4, 4) in game.crosses


def test_victoria_total():
    game = _make_game()
    game.spawn_army(0, (1, 1), {"soldado": 10})
    # P1 pierde su único fuerte y no tiene ejércitos
    game.world.forts[1].owner = 0
    result = game.run_turn([])
    assert result.is_over
    assert result.winner == 0
    assert result.mode == VictoryMode.TOTAL


def test_victoria_por_tiempo():
    game = _make_game(victory_mode=VictoryMode.TIME)
    game.spawn_army(0, (1, 1), {"soldado": 30})
    game.spawn_army(1, (6, 1), {"soldado": 10})
    game.turn = game.config["turnos_limite_default"] - 1
    result = game.run_turn([])
    assert result.is_over
    assert result.winner == 0  # mismo territorio (1 fuerte cada uno), más tropas


def test_partida_sigue_sin_condicion():
    game = _make_game()
    game.spawn_army(0, (1, 1), {"soldado": 10})
    game.spawn_army(1, (6, 1), {"soldado": 10})
    result = game.run_turn([])
    assert not result.is_over
