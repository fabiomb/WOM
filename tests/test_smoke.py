"""Tests de humo: el esqueleto importa y la configuración es válida."""

from wom.core.config import load_ai_config, load_game_config, load_unit_classes

EXPECTED_CLASSES = {"partisano", "soldado", "caballero", "arquero"}
EXPECTED_AI_LEVELS = {"facil", "medio", "dificil"}


def test_unit_classes_load():
    classes = load_unit_classes()
    assert set(classes) == EXPECTED_CLASSES
    for unit_class in classes.values():
        assert unit_class.velocidad > 0
        assert unit_class.ataque > 0
        assert unit_class.defensa > 0


def test_game_config_loads():
    config = load_game_config()
    assert config["max_army_size"] == 100
    assert set(config["xp_recuperacion"]) == {"fort", "town", "campo"}
    assert 0 < config["batalla"]["sigma_aleatoriedad"] < 1


def test_ai_config_loads():
    levels = load_ai_config()
    assert set(levels) == EXPECTED_AI_LEVELS
    for params in levels.values():
        assert params["horizonte"] >= 1
        assert 0 <= params["agresividad"] <= 1


def test_core_does_not_import_pygame():
    """El core (y la AI) deben poder ejecutarse headless, sin pygame.

    Se verifica en un subproceso limpio porque otros tests de esta suite
    sí importan pygame legítimamente (la UI).
    """
    import subprocess
    import sys
    from pathlib import Path

    code = (
        "import sys; "
        "import wom.core.game, wom.core.battle, wom.core.mapgen, "
        "wom.core.tactical, wom.ai.ai_player, wom.ai.tactical_ai, wom.headless, "
        "wom.persistence.savegame, wom.persistence.scenario, "
        "wom.net.protocol, wom.net.orders_codec, wom.net.state_hash, "
        "wom.net.transport, wom.net.session, wom.net.config_fingerprint, "
        "wom.net.lockstep, wom.net.rules, wom.net.lobby, wom.net.match, "
        "wom.llm.agent, wom.llm.actions, wom.llm.observation, "
        "wom.llm.prompt, wom.llm.backend; "
        "sys.exit(1 if 'pygame' in sys.modules else 0)"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"wom.core/wom.ai/wom.net/wom.llm no deben importar pygame\n{result.stderr}"
    )


def test_server_does_not_import_pygame():
    """El servidor dedicado es stdlib-only: no debe arrastrar pygame.

    Se verifica en un subproceso limpio (otros tests sí importan pygame para la
    UI). Importa el runnable del servidor y comprueba que pygame no quedó cargado.
    """
    import subprocess
    import sys
    from pathlib import Path

    code = (
        "import sys; "
        "import server.main, server.config, server.game_server; "
        "sys.exit(1 if 'pygame' in sys.modules else 0)"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"el paquete server/ no debe importar pygame\n{result.stderr}"
    )
