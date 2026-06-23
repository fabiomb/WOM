"""Config del servidor dedicado y el comando `--check` del runnable."""

import pytest

from server.config import DEFAULT_PORT, ServerConfig, load_config
from server.main import main, summary


def test_defaults_when_no_path():
    cfg = load_config(None)
    assert cfg.port == DEFAULT_PORT
    assert cfg.require_password is False
    assert cfg.max_connections > 0


def test_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_config(tmp_path / "no-existe.toml")


def test_loads_and_overrides_from_toml(tmp_path):
    toml = tmp_path / "server.toml"
    toml.write_text(
        "[server]\n"
        'name = "Test"\n'
        "port = 51000\n"
        "[auth]\n"
        "require_password = true\n"
        'password = "secreta"\n'
        "[limits]\n"
        "max_matches = 7\n",
        encoding="utf-8",
    )
    cfg = load_config(toml)
    assert cfg.name == "Test"
    assert cfg.port == 51000
    assert cfg.require_password is True
    assert cfg.password == "secreta"
    assert cfg.max_matches == 7
    # Las claves ausentes conservan los defaults.
    assert cfg.max_connections == ServerConfig().max_connections


def test_partial_sections_use_defaults(tmp_path):
    toml = tmp_path / "server.toml"
    toml.write_text('[server]\nname = "Solo nombre"\n', encoding="utf-8")
    cfg = load_config(toml)
    assert cfg.name == "Solo nombre"
    assert cfg.port == DEFAULT_PORT


def test_invalid_toml_raises(tmp_path):
    toml = tmp_path / "bad.toml"
    toml.write_text("esto no es toml = = =\n", encoding="utf-8")
    with pytest.raises(ValueError):  # tomllib.TOMLDecodeError es ValueError
        load_config(toml)


def test_summary_mentions_name_and_port():
    text = summary(ServerConfig(name="Servidor X", port=12345))
    assert "Servidor X" in text
    assert "12345" in text


def test_main_check_returns_zero(capsys):
    assert main(["--check"]) == 0
    out = capsys.readouterr().out
    assert "OK" in out


def test_main_without_loop_reports_and_exits(capsys):
    assert main([]) == 0
    out = capsys.readouterr().out
    assert "fase posterior" in out


def test_main_bad_config_returns_two(tmp_path, capsys):
    assert main(["--config", str(tmp_path / "nope.toml")]) == 2
    assert "Error de configuración" in capsys.readouterr().err
