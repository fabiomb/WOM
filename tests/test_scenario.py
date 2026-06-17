"""Tests del contenedor `.wom`: roundtrip, info liviana y armado de partida."""

import pytest

from wom.core.game import Player
from wom.core.victory import VictoryMode
from wom.core.worldmap import Fort, Terrain, Town, WorldMap
from wom.persistence.scenario import (
    SCENARIO_FORMAT_VERSION,
    ScenarioDoc,
    build_game,
    list_maps,
    load_scenario,
    save_scenario,
    scenario_info,
)


def _world() -> WorldMap:
    tiles = [[Terrain.PLAINS] * 12 for _ in range(10)]
    tiles[3][4] = Terrain.WATER
    world = WorldMap(width=12, height=10, tiles=tiles)
    world.forts = [Fort((1, 1), owner=0), Fort((10, 8), owner=1)]
    world.towns = [Town((5, 5))]
    return world


def _doc(image: bytes | None = b"PNGDATA") -> ScenarioDoc:
    return ScenarioDoc(
        world=_world(),
        players=[
            Player(0, "Jugador"),
            Player(1, "Rival", is_ai=True, ai_level="dificil"),
        ],
        army_specs=[
            {"owner": 0, "position": [1, 1], "composition": {"soldado": 10}},
            {"owner": 1, "position": [10, 8], "composition": {"arquero": 8}},
        ],
        title="Batalla de prueba",
        description="Un escenario de test",
        victory_mode=VictoryMode.FLAGS,
        ai_level="dificil",
        image_bytes=image,
    )


def test_roundtrip(tmp_path):
    path = save_scenario(_doc(), directory=tmp_path)
    assert path.suffix == ".wom"
    loaded = load_scenario(path)
    original = _doc()
    assert loaded.world.to_dict() == original.world.to_dict()
    assert loaded.army_specs == original.army_specs
    assert loaded.title == "Batalla de prueba"
    assert loaded.victory_mode is VictoryMode.FLAGS
    assert loaded.ai_level == "dificil"
    assert loaded.players[1].ai_level == "dificil"
    assert loaded.image_bytes == b"PNGDATA"


def test_roundtrip_sin_imagen(tmp_path):
    path = save_scenario(_doc(image=None), directory=tmp_path)
    loaded = load_scenario(path)
    assert loaded.image_bytes is None


def test_scenario_info_no_extrae_imagen(tmp_path):
    path = save_scenario(_doc(), name="info", directory=tmp_path)
    info = scenario_info(path)
    assert info["title"] == "Batalla de prueba"
    assert info["has_image"] is True
    assert "image_bytes" not in info  # solo metadata liviana


def test_build_game_escenario_completo(tmp_path):
    game = build_game(_doc())
    assert game.turn == 0
    assert {a.position for a in game.armies} == {(1, 1), (10, 8)}
    assert game.army_at((1, 1)).owner == 0
    assert game.army_at((10, 8)).composition == {"arquero": 8}
    assert game.victory_mode is VictoryMode.FLAGS
    assert game.players[1].ai_level == "dificil"
    assert game.players[0].food == game.config["comida_inicial"]


def test_build_game_con_overrides(tmp_path):
    """'Cargar mapa': el menú impone jugadores y victoria; se reusa el mapa."""
    doc = _doc()
    players = [Player(0, "Humano"), Player(1, "AI (facil)", is_ai=True, ai_level="facil")]
    game = build_game(doc, players=players, victory_mode=VictoryMode.TOTAL)
    assert game.victory_mode is VictoryMode.TOTAL
    assert game.players[1].ai_level == "facil"
    assert {a.position for a in game.armies} == {(1, 1), (10, 8)}


def test_partida_jugable_tras_cargar(tmp_path):
    """La partida armada desde un `.wom` corre un turno sin romper."""
    game = build_game(_doc())
    result = game.run_turn([])
    assert game.turn == 1
    assert result is not None


def test_list_maps(tmp_path):
    save_scenario(_doc(), name="uno", directory=tmp_path)
    save_scenario(_doc(), name="dos", directory=tmp_path)
    maps = list_maps([tmp_path])
    assert {p.name for p in maps} == {"uno.wom", "dos.wom"}


def test_list_maps_carpeta_inexistente(tmp_path):
    assert list_maps([tmp_path / "no_existe"]) == []


def test_load_rechaza_version_incompatible(tmp_path, monkeypatch):
    import wom.persistence.scenario as scenario

    monkeypatch.setattr(scenario, "SCENARIO_FORMAT_VERSION", SCENARIO_FORMAT_VERSION + 1)
    path = save_scenario(_doc(), name="futuro", directory=tmp_path)
    monkeypatch.setattr(scenario, "SCENARIO_FORMAT_VERSION", SCENARIO_FORMAT_VERSION)
    with pytest.raises(ValueError, match="formato"):
        load_scenario(path)
