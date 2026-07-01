"""La huella de config no debe depender del fin de línea (CRLF vs LF).

Regresión: con `core.autocrlf` un checkout podía dejar los JSON de balance en
CRLF y otro en LF; como la huella se comparaba sobre los bytes crudos, un
cliente empaquetado con CRLF y un servidor con LF —los mismos datos— se
rechazaban en el handshake ("la configuración de balance no coincide").
"""

from __future__ import annotations

import hashlib

from wom.net import config_fingerprint as cf


def _hash_dir(tmp_path, classes: bytes, game: bytes) -> str:
    """Calcula la huella apuntando `CONFIG_DIR` a un directorio temporal."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "classes.json").write_bytes(classes)
    (tmp_path / "game.json").write_bytes(game)
    original = cf.CONFIG_DIR
    try:
        cf.CONFIG_DIR = tmp_path
        return cf.config_fingerprint()
    finally:
        cf.CONFIG_DIR = original


def test_normalized_bytes_iguala_crlf_y_lf():
    lf = b'{\n  "a": 1\n}\n'
    crlf = b'{\r\n  "a": 1\r\n}\r\n'
    assert cf._normalized_bytes  # existe
    # Escribimos a archivos temporales para ejercitar la lectura real.
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as d:
        p_lf = Path(d) / "lf.json"
        p_crlf = Path(d) / "crlf.json"
        p_lf.write_bytes(lf)
        p_crlf.write_bytes(crlf)
        assert cf._normalized_bytes(p_lf) == cf._normalized_bytes(p_crlf)


def test_fingerprint_invariante_al_fin_de_linea(tmp_path):
    classes_lf = b'{\n  "espadachin": {"ataque": 5}\n}\n'
    game_lf = b'{\n  "batalla": {"umbral": 0.3}\n}\n'
    classes_crlf = classes_lf.replace(b"\n", b"\r\n")
    game_crlf = game_lf.replace(b"\n", b"\r\n")

    h_lf = _hash_dir(tmp_path / "lf", classes_lf, game_lf)
    h_crlf = _hash_dir(tmp_path / "crlf", classes_crlf, game_crlf)

    assert h_lf == h_crlf


def test_fingerprint_cambia_con_el_contenido(tmp_path):
    base = _hash_dir(tmp_path / "a", b'{"ataque": 5}\n', b'{"umbral": 0.3}\n')
    otro = _hash_dir(tmp_path / "b", b'{"ataque": 6}\n', b'{"umbral": 0.3}\n')
    assert base != otro


def test_fingerprint_es_sha1_hex():
    h = cf.config_fingerprint()
    assert len(h) == len(hashlib.sha1(b"").hexdigest())
    int(h, 16)  # es hex válido
