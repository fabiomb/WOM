"""Punto de entrada del servidor dedicado de WOM.

Stdlib-only (sin pygame ni assets). Uso:

    python -m server [--config server.toml] [--check]

`--check` valida la configuración y el entorno (que las capas puras de `wom/`
estén disponibles, sin pygame) y sale sin abrir el puerto: sirve para verificar
una instalación. El **loop de atención** (aceptar conexiones, lobby y partidas)
se implementa en una fase posterior (ver `docs/server.md` §12, fase S4).
"""

from __future__ import annotations

import argparse
import logging
import sys

from server.config import ServerConfig, load_config


def summary(cfg: ServerConfig) -> str:
    """Resumen legible de la configuración efectiva (para logs y `--check`)."""
    acceso = "con contraseña" if cfg.require_password else "entrada libre"
    aleatorio = "sí" if cfg.allow_random else "no"
    return (
        f"Servidor WOM «{cfg.name}»\n"
        f"  escucha    : {cfg.host}:{cfg.port}\n"
        f"  acceso     : {acceso}\n"
        f"  límites    : {cfg.max_connections} conexiones "
        f"({cfg.max_connections_per_ip}/IP), {cfg.max_matches} partidas\n"
        f"  escenarios : {cfg.scenarios_dir} (aleatorio: {aleatorio})"
    )


def run_check(cfg: ServerConfig) -> int:
    """Valida que las capas puras necesarias importen (sin pygame) y reporta."""
    import wom.core.game  # noqa: F401
    import wom.net.protocol  # noqa: F401
    import wom.persistence.scenario  # noqa: F401

    print(summary(cfg))
    print("OK: entorno y configuración válidos.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="server", description="Servidor online de WOM"
    )
    parser.add_argument("--config", help="ruta a server.toml (opcional)")
    parser.add_argument(
        "--check",
        action="store_true",
        help="valida configuración y entorno, y sale (no abre el puerto)",
    )
    args = parser.parse_args(argv)

    try:
        cfg = load_config(args.config)
    except (OSError, ValueError) as exc:
        print(f"Error de configuración: {exc}", file=sys.stderr)
        return 2

    if args.check:
        return run_check(cfg)

    _configure_logging(cfg)
    print(summary(cfg))
    # Import perezoso: así `--check` (y los tests de config) no cargan el core.
    from server.game_server import GameServer

    server = GameServer(cfg)
    print(f"Escuchando en {cfg.host}:{server.port}. Ctrl+C para detener.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.stop()
    return 0


def _configure_logging(cfg: ServerConfig) -> None:
    level = getattr(logging, cfg.log_level.upper(), logging.INFO)
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if cfg.log_file:
        handlers.append(logging.FileHandler(cfg.log_file, encoding="utf-8"))
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=handlers,
    )


if __name__ == "__main__":
    raise SystemExit(main())
