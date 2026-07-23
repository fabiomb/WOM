"""Tests del jugador-LLM (`wom/llm/`): observación, traducción de acciones,
extracción tolerante de JSON, parseo de respuestas de cada backend y el agente
con un backend falso. Todo headless, sin red ni LLM real.
"""

from __future__ import annotations

import pytest

from wom.core.game import Game, Player
from wom.core.mapgen import MapParams
from wom.core.orders import (
    CreateArmyOrder,
    MergeArmyOrder,
    MoveOrder,
    SplitArmyOrder,
    TransferTroopsOrder,
)
from wom.core.victory import VictoryMode
from wom.llm.actions import translate_actions
from wom.llm.agent import LLMPlayer, extract_actions
from wom.llm.backend import (
    Anthropic,
    BackendConfig,
    LLMBackend,
    OpenAICompatible,
    make_backend,
    parse_anthropic_response,
    parse_gemini_response,
    parse_openai_response,
)
from wom.llm.observation import build_observation, render_text


def make_game(seed: int = 7) -> Game:
    players = [Player(0, "Host"), Player(1, "Cliente")]
    return Game.new(
        MapParams(width=12, height=12, n_forts=4, n_towns=4, seed=seed),
        players,
        VictoryMode.TOTAL,
    )


def my_army(game: Game, player_id: int):
    return next(a for a in game.armies if a.owner == player_id)


# --- observación ----------------------------------------------------------


def test_observation_has_core_fields():
    game = make_game()
    obs = build_observation(game, 1)
    assert obs["you"]["player_id"] == 1
    assert obs["turn"] == game.turn
    assert obs["map_size"] == [12, 12]
    assert obs["my_armies"], "el jugador arranca con un ejército"
    assert all(a["pos"] for a in obs["my_armies"])
    # Los enemigos no exponen su composición exacta, solo el total.
    for enemy in obs["enemy_armies"]:
        assert "composition" not in enemy
        assert "total" in enemy


def test_observation_hides_enemy_fort_reserves():
    game = make_game()
    obs = build_observation(game, 1)
    for fort in obs["forts"]:
        if fort["owner"] == "enemigo":
            assert fort["reserve_total"] is None


def test_render_text_is_nonempty_and_mentions_turn():
    game = make_game()
    text = render_text(game, 1)
    assert "Turno" in text
    assert "Mapa 12x12" in text
    assert "Leyenda" in text


# --- traducción de acciones ----------------------------------------------


def test_move_translates_to_path():
    game = make_game()
    army = my_army(game, 1)
    # Un destino alcanzable: un vecino transitable.
    dest = next(n for n in game.world.neighbors(army.position))
    orders, warnings = translate_actions(
        [{"action": "move", "army": army.id, "to": list(dest)}], game, 1
    )
    assert warnings == []
    assert len(orders) == 1
    assert isinstance(orders[0], MoveOrder)
    assert orders[0].army_id == army.id
    assert orders[0].path[-1] == dest


def test_move_rejects_enemy_army_and_water():
    game = make_game()
    enemy = my_army(game, 0)  # ejército del rival
    orders, warnings = translate_actions(
        [{"action": "move", "army": enemy.id, "to": [0, 0]}], game, 1
    )
    assert orders == []
    assert warnings and "no es tuyo" in warnings[0]


def test_move_to_same_tile_is_rejected():
    game = make_game()
    army = my_army(game, 1)
    orders, warnings = translate_actions(
        [{"action": "move", "army": army.id, "to": list(army.position)}], game, 1
    )
    assert orders == []
    assert "ya está" in warnings[0]


def test_create_requires_own_fort_with_reserve():
    game = make_game()
    fort = next(f for f in game.world.forts if f.owner == 1)
    fort.reserve = {"soldado": 5}
    # Sacamos cualquier ejército parado encima para que la creación sea válida.
    game.armies = [a for a in game.armies if a.position != fort.position]
    orders, warnings = translate_actions(
        [{"action": "create", "fort": list(fort.position)}], game, 1
    )
    assert warnings == []
    assert orders == [CreateArmyOrder(position=fort.position)]


def test_create_on_enemy_fort_rejected():
    game = make_game()
    enemy_fort = next(f for f in game.world.forts if f.owner == 0)
    orders, warnings = translate_actions(
        [{"action": "create", "fort": list(enemy_fort.position)}], game, 1
    )
    assert orders == []
    assert "no es un fuerte propio" in warnings[0]


def test_split_validates_troops_and_remainder():
    game = make_game()
    army = my_army(game, 1)
    army.composition = {"soldado": 20}
    army.position = _open_tile_with_free_neighbor(game)
    orders, warnings = translate_actions(
        [{"action": "split", "source": army.id, "detach": {"soldado": 5}}], game, 1
    )
    assert warnings == []
    assert orders == [SplitArmyOrder(source_id=army.id, composition=(("soldado", 5),))]
    # Pedir más de lo disponible se rechaza.
    orders, warnings = translate_actions(
        [{"action": "split", "source": army.id, "detach": {"soldado": 999}}], game, 1
    )
    assert orders == []


def test_merge_and_transfer_require_adjacency():
    game = make_game()
    a = my_army(game, 1)
    # Creamos un segundo ejército propio adyacente.
    nb = next(n for n in game.world.neighbors(a.position) if game.army_at(n) is None)
    b = game.spawn_army(1, nb, {"soldado": 10})
    merge, warnings = translate_actions(
        [{"action": "merge", "source": b.id, "target": a.id}], game, 1
    )
    assert warnings == []
    assert merge == [MergeArmyOrder(source_id=b.id, target_id=a.id)]

    transfer, warnings = translate_actions(
        [{"action": "transfer", "source": b.id, "target": a.id, "troops": {"soldado": 3}}],
        game,
        1,
    )
    assert warnings == []
    assert transfer == [
        TransferTroopsOrder(source_id=b.id, target_id=a.id, composition=(("soldado", 3),))
    ]


def test_move_accepts_hash_prefixed_id():
    game = make_game()
    army = my_army(game, 1)
    dest = next(n for n in game.world.neighbors(army.position))
    orders, warnings = translate_actions(
        [{"action": "move", "army": f"#{army.id}", "to": list(dest)}], game, 1
    )
    assert warnings == []
    assert orders and orders[0].army_id == army.id


def test_unknown_action_is_warned_not_crashed():
    game = make_game()
    orders, warnings = translate_actions([{"action": "nuke"}, {"action": "wait"}], game, 1)
    assert orders == []
    assert any("desconocida" in w for w in warnings)


def test_move_accepts_id_variants_and_key_synonyms():
    """Los modelos chicos envuelven el id en texto o cambian la clave: se
    aceptan "ejército 7", "army_id"/"id"/"unit", y la posición [x, y]."""
    game = make_game()
    army = my_army(game, 1)
    dest = next(n for n in game.world.neighbors(army.position))
    variants = [
        {"action": "move", "army": f"ejército {army.id}", "to": list(dest)},
        {"action": "move", "army_id": army.id, "to": list(dest)},
        {"action": "move", "id": str(army.id), "to": list(dest)},
        {"action": "move", "unit": f"#{army.id}", "to": list(dest)},
        {"action": "move", "army": list(army.position), "to": list(dest)},  # por posición
    ]
    for action in variants:
        orders, warnings = translate_actions([action], game, 1)
        assert warnings == [], f"{action} → {warnings}"
        assert orders and orders[0].army_id == army.id, f"{action}"


def test_bad_army_id_warning_lists_own_ids():
    """El rechazo enseña: incluye los ids propios (vuelve al modelo como
    feedback en el turno siguiente)."""
    game = make_game()
    army = my_army(game, 1)
    orders, warnings = translate_actions(
        [{"action": "move", "army": 999, "to": [1, 1]}], game, 1
    )
    assert orders == []
    assert warnings and "no existe" in warnings[0]
    assert f"#{army.id}" in warnings[0], f"debe listar los ids propios: {warnings[0]}"


def _open_tile_with_free_neighbor(game: Game):
    """Un tile sin sitio cuyo vecino también esté libre (para split)."""
    from wom.core.worldmap import Terrain

    for y in range(game.world.height):
        for x in range(game.world.width):
            pos = (x, y)
            if game.world.terrain_at(pos) != Terrain.PLAINS:
                continue
            if game.world.fort_at(pos) or game.world.town_at(pos):
                continue
            frees = [
                n
                for n in game.world.neighbors(pos)
                if game.army_at(n) is None
                and game.world.fort_at(n) is None
                and game.world.town_at(n) is None
            ]
            if frees:
                # Aseguramos que no haya otro ejército en pos.
                game.armies = [a for a in game.armies if a.position != pos]
                return pos
    raise AssertionError("no se encontró un tile adecuado")


# --- extracción tolerante de JSON ----------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        '[{"action": "wait"}]',
        '```json\n[{"action": "wait"}]\n```',
        'Claro, mi plan:\n[{"action": "wait"}]\n¡Listo!',
        '{"actions": [{"action": "wait"}]}',
        '{"action": "wait"}',
    ],
)
def test_extract_actions_tolerant(text):
    actions = extract_actions(text)
    assert actions == [{"action": "wait"}]


def test_extract_actions_returns_none_on_garbage():
    assert extract_actions("no hay json aquí") is None
    assert extract_actions("") is None


# --- parseo de respuestas de backends ------------------------------------


def test_parse_openai_response():
    data = {"choices": [{"message": {"content": "hola"}}]}
    assert parse_openai_response(data) == "hola"


def test_parse_gemini_response():
    data = {"candidates": [{"content": {"parts": [{"text": "ho"}, {"text": "la"}]}}]}
    assert parse_gemini_response(data) == "hola"


def test_parse_anthropic_response():
    data = {"content": [{"type": "text", "text": "hola"}]}
    assert parse_anthropic_response(data) == "hola"


def test_anthropic_payload_omits_temperature_on_opus_4_8():
    # Opus 4.8 rechaza sampling: temperature debe ausentarse o la API da 400.
    backend = Anthropic(
        BackendConfig(provider="anthropic", model="claude-opus-4-8", api_key="x")
    )
    payload = backend.build_payload("sys", "user")
    assert "temperature" not in payload
    assert "thinking" not in payload


def test_anthropic_payload_keeps_temperature_on_legacy_model():
    backend = Anthropic(
        BackendConfig(provider="anthropic", model="claude-3-5-sonnet", api_key="x")
    )
    payload = backend.build_payload("sys", "user")
    assert payload["temperature"] == pytest.approx(0.3)


def test_anthropic_thinking_enables_adaptive_and_headroom():
    backend = Anthropic(
        BackendConfig(
            provider="anthropic",
            model="claude-opus-4-8",
            api_key="x",
            thinking=True,
            effort="high",
            max_tokens=2048,
        )
    )
    payload = backend.build_payload("sys", "user")
    assert payload["thinking"] == {"type": "adaptive"}
    assert payload["output_config"] == {"effort": "high"}
    assert "temperature" not in payload
    assert payload["max_tokens"] >= 16000  # el pensamiento necesita headroom


def test_make_backend_resolves_defaults(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    backend = make_backend(BackendConfig(provider="ollama", model="gemma3"))
    assert isinstance(backend, OpenAICompatible)
    assert backend.base_url == "http://localhost:11434/v1"


def test_make_backend_reads_api_key_from_env(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "secret")
    config = BackendConfig(provider="gemini", model="gemini-2.5-flash")
    make_backend(config)
    assert config.api_key == "secret"


# --- agente con backend falso --------------------------------------------


class FakeBackend(LLMBackend):
    """Backend que devuelve respuestas predefinidas, sin red."""

    def __init__(self, *responses: str) -> None:
        self._responses = list(responses)
        self.calls: list[tuple[str, str]] = []

    def complete(self, system: str, user: str) -> str:
        self.calls.append((system, user))
        return self._responses.pop(0) if self._responses else "[]"


def test_llm_player_produces_orders():
    game = make_game()
    army = my_army(game, 1)
    dest = next(n for n in game.world.neighbors(army.position))
    backend = FakeBackend(
        f'[{{"action": "move", "army": {army.id}, "to": [{dest[0]}, {dest[1]}]}}]'
    )
    player = LLMPlayer(1, backend)
    orders = player.decide_orders(game)
    assert len(orders) == 1
    assert isinstance(orders[0], MoveOrder)
    assert backend.calls, "el agente consultó el backend"


def test_llm_player_retries_then_passes_on_bad_json():
    game = make_game()
    backend = FakeBackend("no json", "todavía no json")
    player = LLMPlayer(1, backend)
    orders = player.decide_orders(game)
    assert orders == []
    assert len(backend.calls) == 2, "reintenta una vez antes de pasar"


def test_llm_player_feeds_back_previous_warnings():
    """Los descartes de un turno vuelven al modelo en el prompt del siguiente
    (feedback correctivo para modelos chicos)."""
    game = make_game()
    backend = FakeBackend(
        '[{"action": "move", "army": 999, "to": [1, 1]}]',  # turno 1: id inválido
        "[]",  # turno 2: pasa
    )
    player = LLMPlayer(1, backend)
    player.decide_orders(game)
    assert player.last_warnings, "el primer turno debió descartar la acción"
    player.decide_orders(game)
    _system, user = backend.calls[1]
    assert "descartadas" in user and "#999" in user
    assert player.last_observation == user, "expone el prompt tal como se envió"
    # El primer prompt no llevaba feedback (no había turno anterior).
    assert "descartadas" not in backend.calls[0][1]
