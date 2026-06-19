"""Tests del motor de turnos sobre escenarios construidos a mano."""

import random

import pytest

from wom.core.army import Army
from wom.core.config import load_game_config, load_unit_classes
from wom.core.game import Game, Player
from wom.core.orders import (
    CreateArmyOrder,
    MoveOrder,
    SplitArmyOrder,
    TransferTroopsOrder,
)
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


def test_no_hay_retirada_dentro_de_un_fuerte():
    game = _make_game()
    game.config["batalla"]["sigma_aleatoriedad"] = 0.0  # resultado determinista
    fort = Fort(position=(3, 1), owner=1)
    game.world.forts.append(fort)
    attacker = game.spawn_army(0, (2, 1), {"soldado": 100})
    defender = game.spawn_army(1, (3, 1), {"soldado": 50})
    game.run_turn([MoveOrder(army_id=attacker.id, path=((3, 1),))])
    # ratio ~1.33 => DEFENDER_RETREATS, pero está atrincherado: no se mueve
    assert defender in game.armies  # sobrevivió a la batalla
    assert defender.position == (3, 1)  # perdió pero mantiene el fuerte
    assert defender.id not in game.last_retreats
    assert fort.owner == 1  # el atacante no pudo entrar a capturar


def test_turn_limit_termina_la_partida_en_cualquier_modo():
    """El tope de turnos del host (turn_limit) corta la partida con desempate
    por territorio/tropas, incluso en modo conquista total."""
    game = _make_game(victory_mode=VictoryMode.TOTAL)
    game.turn_limit = 2
    game.spawn_army(0, (1, 1), {"soldado": 10})
    game.spawn_army(1, (6, 1), {"soldado": 10})  # ambos vivos: no hay total
    assert not game.run_turn([]).is_over  # turno 1 < 2
    result = game.run_turn([])  # turno 2 == límite
    assert result.is_over and result.mode is VictoryMode.TIME
    # El tope viaja en el estado (lo reconstruye el cliente en red).
    assert Game.from_dict(game.to_dict()).turn_limit == 2


def test_load_state_adopta_estado_in_place():
    """La resincronización del multiplayer: un Game adopta otro estado sin
    recrear el objeto (las referencias del renderer siguen válidas)."""
    a = _make_game()
    a.spawn_army(0, (1, 1), {"soldado": 10})
    a.run_turn([])
    b = _make_game()  # mismo objeto que se va a mutar
    b.load_state(a.to_dict())
    assert b.to_dict() == a.to_dict()
    assert b.turn == a.turn and len(b.armies) == len(a.armies)


def test_transfer_troops_order_se_aplica_en_el_turno():
    """La reorganización diferida del humano en red: TransferTroopsOrder mueve
    tropas entre dos ejércitos aledaños en la fase de órdenes."""
    game = _make_game()
    a = game.spawn_army(0, (2, 1), {"soldado": 20})
    b = game.spawn_army(0, (2, 2), {"soldado": 10})  # aledaño
    game.run_turn(
        [TransferTroopsOrder(a.id, b.id, (("soldado", 5),))]
    )
    assert a.composition["soldado"] == 15
    assert b.composition["soldado"] == 15


def test_split_army_order_se_aplica_en_el_turno():
    game = _make_game()
    army = game.spawn_army(0, (2, 2), {"soldado": 12})
    game.run_turn([SplitArmyOrder(army.id, (("soldado", 4),))])
    assert army.composition["soldado"] == 8
    created = next(a for a in game.armies_of(0) if a.id != army.id)
    assert created.composition == {"soldado": 4}


def test_trabado_por_lados_opuestos_no_escapa():
    game = _make_game()
    army = game.spawn_army(0, (3, 2), {"soldado": 10})
    game.spawn_army(1, (3, 1), {"soldado": 10})  # Norte
    game.spawn_army(1, (3, 3), {"soldado": 10})  # Sur (eje vertical cerrado)
    game.run_turn([MoveOrder(army_id=army.id, path=((2, 2), (1, 2)))])
    assert army.position == (3, 2)  # no pudo escapar lateralmente
    assert army.path == []  # la ruta de fuga se descartó


def test_trabado_en_dos_lados_contiguos_si_escapa():
    game = _make_game()
    army = game.spawn_army(0, (3, 2), {"soldado": 10})
    game.spawn_army(1, (3, 1), {"soldado": 10})  # Norte
    game.spawn_army(1, (2, 2), {"soldado": 10})  # Oeste (lados contiguos)
    game.run_turn([MoveOrder(army_id=army.id, path=((4, 2),))])
    assert army.position == (4, 2)  # lados contiguos no traban: escapa


def test_trabado_puede_atacar_a_quien_lo_rodea():
    game = _make_game()
    game.config["batalla"]["sigma_aleatoriedad"] = 0.0
    army = game.spawn_army(0, (3, 2), {"soldado": 100})
    enemy_n = game.spawn_army(1, (3, 1), {"soldado": 5})  # Norte (lo atacará)
    game.spawn_army(1, (3, 3), {"soldado": 10})  # Sur (cierra el eje)
    game.run_turn([MoveOrder(army_id=army.id, path=((3, 1),))])
    # aunque trabado, puede embestir a un enemigo contiguo: hubo batalla
    assert enemy_n not in game.armies or enemy_n.position != (3, 1)


def test_no_se_escurre_entre_dos_enemigos():
    game = _make_game()
    army = game.spawn_army(0, (1, 2), {"soldado": 10})  # velocidad 3
    game.spawn_army(1, (3, 1), {"soldado": 10})  # Norte de (3,2)
    game.spawn_army(1, (3, 3), {"soldado": 10})  # Sur de (3,2)
    # intenta pasar en línea recta por (3,2), que está trabado
    game.run_turn([MoveOrder(army_id=army.id, path=((2, 2), (3, 2), (4, 2)))])
    assert army.position == (3, 2)  # queda atrapado entre ambos, no se cuela
    assert army.path == []  # no puede continuar la fuga


def test_choques_y_retiradas_del_turno_quedan_registrados():
    game = _make_game()
    game.config["batalla"]["sigma_aleatoriedad"] = 0.0  # resultado determinista
    a = game.spawn_army(0, (2, 1), {"soldado": 100})
    b = game.spawn_army(1, (4, 1), {"soldado": 40})
    game.run_turn([MoveOrder(army_id=a.id, path=((3, 1), (4, 1)))])
    # ratio 2.5 => ATTACKER_WINS; el defensor sobrevive (65% de bajas) y huye
    assert game.last_clashes == [(a.id, b.id)]
    assert b in game.armies
    assert b.id in game.last_retreats
    assert b.position != (4, 1)
    game.run_turn([])  # sin batallas: los registros transitorios se limpian
    assert game.last_clashes == [] and game.last_retreats == set()


def test_aliado_en_el_camino_espera_sin_perder_la_ruta():
    game = _make_game()
    a = game.spawn_army(0, (1, 1), {"soldado": 10})  # velocidad 3
    blocker = game.spawn_army(0, (3, 1), {"soldado": 10})
    game.run_turn([MoveOrder(army_id=a.id, path=((2, 1), (3, 1), (4, 1), (5, 1)))])
    # Frenó ante el aliado pero la ruta sigue viva (antes se cancelaba).
    assert a.position == (2, 1)
    assert a.path == [(3, 1), (4, 1), (5, 1)]
    # El aliado se corre (mueve después de `a` por id: recién libera ahora).
    game.run_turn([MoveOrder(army_id=blocker.id, path=((3, 2),))])
    assert a.position == (2, 1) and a.path  # todavía esperando
    game.run_turn([])  # camino libre: retoma la marcha solo
    assert a.position == (5, 1)
    assert a.path == []


def test_cruce_de_aliados_pasa_en_orden_de_id():
    game = _make_game()
    first = game.spawn_army(0, (3, 1), {"soldado": 10})  # id menor: mueve antes
    second = game.spawn_army(0, (1, 1), {"soldado": 10})
    game.run_turn([
        MoveOrder(army_id=first.id, path=((3, 2),)),
        MoveOrder(army_id=second.id, path=((2, 1), (3, 1), (4, 1))),
    ])
    # `first` se corrió primero y dejó el paso libre en el mismo turno.
    assert first.position == (3, 2)
    assert second.position == (4, 1)


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


def test_movimiento_cruza_puentes():
    game = _make_game()
    # río vertical en x=3 con un puente en (3, 1)
    for y in range(game.world.height):
        game.world.tiles[y][3] = Terrain.WATER
    game.world.tiles[1][3] = Terrain.BRIDGE_H
    army = game.spawn_army(0, (2, 1), {"soldado": 10})  # velocidad 3
    game.run_turn([MoveOrder(army_id=army.id, path=((3, 1), (4, 1)))])
    assert army.position == (4, 1)  # el puente cuesta 1: cruzó entero


def test_no_se_escapa_de_un_puente_por_el_lateral():
    game = _make_game()
    # río vertical en x=3, puente en (3, 1) y tierra al norte en (3, 0)
    for y in range(game.world.height):
        game.world.tiles[y][3] = Terrain.WATER
    game.world.tiles[1][3] = Terrain.BRIDGE_H
    game.world.tiles[0][3] = Terrain.PLAINS

    on_bridge = game.spawn_army(0, (3, 1), {"soldado": 10})
    game.run_turn([MoveOrder(army_id=on_bridge.id, path=((3, 0),))])
    assert on_bridge.position == (3, 1)  # la orden lateral se descartó
    assert on_bridge.path == []

    on_land = game.spawn_army(0, (3, 0), {"soldado": 10})
    game.run_turn([MoveOrder(army_id=on_land.id, path=((3, 1),))])
    assert on_land.position == (3, 0)  # tampoco se entra por el lateral


def test_fusion_de_ejercitos():
    game = _make_game()
    a = game.spawn_army(0, (2, 1), {"soldado": 30, "arquero": 10})
    b = game.spawn_army(0, (3, 1), {"soldado": 20})
    a.xp, b.xp = 100, 40
    a.food, b.food = 100, 40
    assert game.merge_armies(a.id, b.id)
    assert game.army_by_id(a.id) is None  # el source se integró
    assert game.crosses == []  # sin cruz: nadie murió
    assert b.composition == {"soldado": 50, "arquero": 10}
    # XP y comida: promedio ponderado por tropas (40 de a, 20 de b)
    assert b.xp == round((100 * 40 + 40 * 20) / 60)
    assert b.food == round((100 * 40 + 40 * 20) / 60)


def test_fusion_invalida():
    game = _make_game()
    a = game.spawn_army(0, (2, 1), {"soldado": 60})
    far = game.spawn_army(0, (5, 1), {"soldado": 10})
    enemy = game.spawn_army(1, (3, 1), {"soldado": 10})
    big = game.spawn_army(0, (2, 2), {"soldado": 60})
    assert not game.merge_armies(a.id, far.id)    # no son aledaños
    assert not game.merge_armies(a.id, enemy.id)  # distinto dueño
    assert not game.merge_armies(a.id, big.id)    # 120 > max_army_size
    assert not game.merge_armies(a.id, a.id)      # consigo mismo
    assert len(game.armies) == 4  # nada cambió


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
    assert (4, 4, 0) in game.crosses  # cruz con el turno de la muerte


def test_las_cruces_desaparecen_tras_su_vida_util():
    game = _make_game()
    army = game.spawn_army(0, (4, 4), {"soldado": 10})
    army.xp = 0
    game.run_turn([])  # muere en el turno 0
    lifetime = game.config["turnos_cruz"]
    for _ in range(lifetime - 1):
        game.run_turn([])
    assert (4, 4, 0) in game.crosses  # edad == turnos_cruz: último turno visible
    game.run_turn([])
    assert game.crosses == []  # superó la vida útil: el mapa queda limpio


def test_victoria_total():
    game = _make_game()
    game.spawn_army(0, (1, 1), {"soldado": 10})
    # P1 pierde su único fuerte y no tiene ejércitos
    game.world.forts[1].owner = 0
    result = game.run_turn([])
    assert result.is_over
    assert result.winner == 0
    assert result.mode == VictoryMode.TOTAL


def test_ffa_cuatro_jugadores_gana_el_ultimo():
    """Free-for-all de 4: la victoria total es del último jugador con presencia
    (ejército o fuerte); los demás eliminados no empatan."""
    width, height = 8, 5
    tiles = [[Terrain.PLAINS] * width for _ in range(height)]
    world = WorldMap(width=width, height=height, tiles=tiles)
    for pid, pos in enumerate([(0, 0), (7, 0), (0, 4), (7, 4)]):
        world.forts.append(Fort(position=pos, owner=pid))
    game = Game(
        world=world,
        players=[Player(i, f"P{i}") for i in range(4)],
        armies=[],
        classes=load_unit_classes(),
        config=load_game_config(),
        victory_mode=VictoryMode.TOTAL,
        rng=random.Random(1),
        seed=1,
    )
    game._spawn_initial_armies()
    assert len(game.armies) == 4  # un ejército inicial por jugador
    # El jugador 0 conquista los fuertes de 1, 2 y 3 y elimina sus ejércitos.
    for pid in (1, 2, 3):
        for fort in world.forts:
            if fort.owner == pid:
                fort.owner = 0
        for army in list(game.armies):
            if army.owner == pid:
                game.armies.remove(army)
    result = game.run_turn([])
    assert result.is_over and result.winner == 0
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


def test_transferencia_parcial_de_tropas():
    game = _make_game()
    a = game.spawn_army(0, (2, 1), {"soldado": 30, "arquero": 10})
    b = game.spawn_army(0, (3, 1), {"soldado": 20})
    a.xp, b.xp = 100, 40
    a.food, b.food = 80, 40
    assert game.transfer_troops(a.id, b.id, {"soldado": 10, "arquero": 5})
    assert a.composition == {"soldado": 20, "arquero": 5}
    assert b.composition == {"soldado": 30, "arquero": 5}
    # XP y comida del destino: promedio ponderado (20 propias, 15 movidas)
    assert b.xp == round((40 * 20 + 100 * 15) / 35)
    assert b.food == round((40 * 20 + 80 * 15) / 35)
    # XP y comida del origen no cambian; el origen sigue vivo
    assert a.xp == 100 and a.food == 80
    assert game.army_by_id(a.id) is not None


def test_transferencia_total_equivale_a_fusion():
    game = _make_game()
    a = game.spawn_army(0, (2, 1), {"soldado": 40})
    b = game.spawn_army(0, (3, 1), {"soldado": 20})
    assert game.transfer_troops(a.id, b.id, {"soldado": 40})
    assert game.army_by_id(a.id) is None  # vacío: se integró sin cruz
    assert game.crosses == []
    assert b.composition == {"soldado": 60}


def test_transferencia_invalida():
    game = _make_game()
    a = game.spawn_army(0, (2, 1), {"soldado": 60})
    b = game.spawn_army(0, (3, 1), {"soldado": 60})
    assert not game.transfer_troops(a.id, b.id, {"soldado": 70})  # no las tiene
    assert not game.transfer_troops(a.id, b.id, {"soldado": 50})  # destino > max
    assert not game.transfer_troops(a.id, b.id, {})               # nada que mover
    assert not game.transfer_troops(a.id, b.id, {"soldado": 0})
    assert a.composition == {"soldado": 60} and b.composition == {"soldado": 60}


def test_dividir_ejercito():
    game = _make_game()
    a = game.spawn_army(0, (2, 1), {"soldado": 30, "arquero": 10})
    a.xp, a.food = 77, 55
    new = game.split_army(a.id, {"arquero": 10, "soldado": 5})
    assert new is not None and new.owner == 0
    assert abs(new.position[0] - 2) + abs(new.position[1] - 1) == 1  # aledaño
    assert new.composition == {"arquero": 10, "soldado": 5}
    assert a.composition == {"soldado": 25, "arquero": 0}
    assert new.xp == 77 and new.food == 55  # hereda XP y comida


def test_dividir_invalido():
    game = _make_game()
    a = game.spawn_army(0, (2, 1), {"soldado": 30})
    assert game.split_army(a.id, {"soldado": 30}) is None  # no puede irse todo
    assert game.split_army(a.id, {"soldado": 0}) is None   # nada que mover
    assert game.split_army(a.id, {"soldado": 40}) is None  # no las tiene
    assert len(game.armies) == 1


def test_dividir_sin_tile_libre():
    game = _make_game()
    a = game.spawn_army(0, (1, 0), {"soldado": 30})  # borde superior
    game.spawn_army(0, (0, 0), {"soldado": 5})
    game.spawn_army(0, (2, 0), {"soldado": 5})
    game.spawn_army(0, (1, 1), {"soldado": 5})
    assert game.split_army(a.id, {"soldado": 10}) is None
    assert a.composition == {"soldado": 30}  # nada cambió
