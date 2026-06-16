"""Determinismo del lockstep: dos partidas idénticas + las mismas órdenes por
turno producen exactamente el mismo estado.

Es la base de la metodología de red (`docs/multiplayer.md` §2-3): por la red solo
viajan las órdenes; cada cliente las aplica sobre su propia copia del juego y
ambos deben converger al mismo estado y al mismo digest. Las órdenes se generan
con la AI (gameplay realista, con batallas) y se pasan por el codec de red
(encode→decode) para ejercitar el camino completo de un turno en red.
"""

from wom.ai.ai_player import AIPlayer
from wom.core.game import Game, Player
from wom.core.mapgen import MapParams
from wom.core.victory import VictoryMode
from wom.net.orders_codec import decode_orders, encode_orders
from wom.net.state_hash import state_digest


# facil vs facil ("ataca casi siempre"): garantiza que haya batallas — el
# camino con RNG, lo más sensible al determinismo. La seed 1 produce combate y
# termina la partida en pocos turnos.
LOCKSTEP_LEVEL = "facil"
LOCKSTEP_SEED = 1


def _new_game(seed: int) -> Game:
    players = [
        Player(0, "Host", is_ai=True, ai_level=LOCKSTEP_LEVEL),
        Player(1, "Cliente", is_ai=True, ai_level=LOCKSTEP_LEVEL),
    ]
    return Game.new(MapParams(22, 15, 3, 4, seed), players, VictoryMode.TOTAL)


def test_initial_state_matches_via_to_dict():
    """Dos Game.new con la misma seed/params arrancan idénticos (regenerable)."""
    assert _new_game(7).to_dict() == _new_game(7).to_dict()


def test_lockstep_digests_stay_in_sync():
    host_game = _new_game(LOCKSTEP_SEED)
    client_game = _new_game(LOCKSTEP_SEED)
    # Las dos AI deciden las órdenes de cada jugador; en una partida real cada
    # cliente decide las de SU humano y se las envía al par. Acá generamos las
    # de ambos desde una sola copia y las aplicamos a las dos, que es justo lo
    # que recibe cada cliente: el mismo conjunto de órdenes para el turno.
    ai0 = AIPlayer(0, level=LOCKSTEP_LEVEL)
    ai1 = AIPlayer(1, level=LOCKSTEP_LEVEL)

    assert state_digest(host_game) == state_digest(client_game)

    max_turns = 60
    for _ in range(max_turns):
        orders = ai0.decide_orders(host_game) + ai1.decide_orders(host_game)
        # Round-trip por el codec de red, como viajarían por el cable.
        wire = decode_orders(encode_orders(orders))

        host_result = host_game.run_turn(list(wire))
        client_result = client_game.run_turn(list(wire))

        assert state_digest(host_game) == state_digest(client_game), (
            f"desincronización en el turno {host_game.turn}"
        )
        assert host_game.to_dict() == client_game.to_dict()
        assert host_result == client_result
        if host_result.is_over:
            break

    # El test no sirve si nunca pasó nada: tiene que haber habido combate
    # (el camino con RNG: batallas), que es lo más sensible al determinismo.
    assert host_game.battles_fought > 0
