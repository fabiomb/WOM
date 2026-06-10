"""Tests de guardado/carga: roundtrip exacto y continuación determinista."""

import json

import pytest

from wom.ai.ai_player import AIPlayer
from wom.core.game import Game, Player
from wom.core.mapgen import MapParams
from wom.core.victory import VictoryMode
from wom.persistence.savegame import (
    SAVE_FORMAT_VERSION,
    list_saves,
    load_game,
    save_game,
    save_info,
)


def _new_game(seed: int = 77) -> Game:
    players = [
        Player(0, "AI Rojo", is_ai=True, ai_level="medio"),
        Player(1, "AI Azul", is_ai=True, ai_level="dificil"),
    ]
    return Game.new(MapParams(seed=seed), players, VictoryMode.TIME)


def _play_turns(game: Game, turns: int) -> None:
    """Avanza la partida con AIs nuevas (sin memoria previa)."""
    ais = [AIPlayer(p.id, p.ai_level) for p in game.players]
    for _ in range(turns):
        orders = [order for ai in ais for order in ai.decide_orders(game)]
        if game.run_turn(orders).is_over:
            break


def test_roundtrip_estado_identico(tmp_path):
    game = _new_game()
    _play_turns(game, 5)
    path = save_game(game, name="test", directory=tmp_path)
    loaded = load_game(path)
    assert loaded.to_dict() == game.to_dict()
    assert loaded.rng.getstate() == game.rng.getstate()
    assert loaded.players[1].ai_level == "dificil"


def test_continuacion_deterministica_tras_cargar(tmp_path):
    """Guardar, seguir jugando y cargar+jugar deben dar la misma partida."""
    game = _new_game()
    _play_turns(game, 5)
    path = save_game(game, name="mitad", directory=tmp_path)

    _play_turns(game, 5)  # rama A: la partida original sigue
    loaded = load_game(path)
    _play_turns(loaded, 5)  # rama B: la partida cargada sigue

    assert loaded.to_dict() == game.to_dict()


def test_save_con_timestamp_y_list_saves(tmp_path):
    game = _new_game()
    older = save_game(game, name="vieja", directory=tmp_path)
    newer = save_game(game, directory=tmp_path)  # nombre por timestamp
    assert newer.name.startswith("partida_")
    saves = list_saves(directory=tmp_path)
    assert saves[0] in (newer, older) and len(saves) == 2


def test_save_info(tmp_path):
    game = _new_game()
    _play_turns(game, 3)
    path = save_game(game, name="resumen", directory=tmp_path)
    info = save_info(path)
    assert info["name"] == "resumen"
    assert info["turn"] == game.turn
    assert info["players"] == ["AI Rojo", "AI Azul"]


def test_load_rechaza_version_incompatible(tmp_path):
    path = tmp_path / "futuro.json"
    path.write_text(
        json.dumps({"format_version": SAVE_FORMAT_VERSION + 1, "game": {}}),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="formato"):
        load_game(path)


def test_list_saves_sin_carpeta(tmp_path):
    assert list_saves(directory=tmp_path / "no_existe") == []
