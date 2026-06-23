"""Configuración del servidor dedicado (`server.toml`).

Stdlib-only: usa `tomllib` (incluido en la stdlib desde Python 3.11). Define
`ServerConfig` con valores por defecto razonables; si el archivo no existe se
lanza un error claro, y si falta una sección/clave se usa el default.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

DEFAULT_PORT = 50000


@dataclass(frozen=True)
class ServerConfig:
    """Parámetros del servidor, mapeados 1:1 con las secciones de `server.toml`.

    `[server]` host/port/name · `[auth]` require_password/password ·
    `[limits]` conexiones, partidas, anti-DDOS · `[maps]` escenarios/aleatorio ·
    `[logging]` nivel/archivo.
    """

    host: str = "0.0.0.0"
    port: int = DEFAULT_PORT
    name: str = "Servidor WOM"
    require_password: bool = False
    password: str = ""
    max_connections: int = 200
    max_connections_per_ip: int = 8
    max_matches: int = 20
    handshake_timeout: float = 10.0
    msg_rate: float = 30.0
    msg_burst: int = 60
    scenarios_dir: str = "data/scenarios"
    allow_random: bool = True
    log_level: str = "info"
    log_file: str = ""

    @classmethod
    def from_toml(cls, data: dict) -> "ServerConfig":
        """Construye la config desde el dict ya parseado de `server.toml`."""
        server = data.get("server", {})
        auth = data.get("auth", {})
        limits = data.get("limits", {})
        maps = data.get("maps", {})
        logging_ = data.get("logging", {})
        base = cls()  # defaults para las claves ausentes
        return cls(
            host=str(server.get("host", base.host)),
            port=int(server.get("port", base.port)),
            name=str(server.get("name", base.name)),
            require_password=bool(auth.get("require_password", base.require_password)),
            password=str(auth.get("password", base.password)),
            max_connections=int(limits.get("max_connections", base.max_connections)),
            max_connections_per_ip=int(
                limits.get("max_connections_per_ip", base.max_connections_per_ip)
            ),
            max_matches=int(limits.get("max_matches", base.max_matches)),
            handshake_timeout=float(limits.get("handshake_timeout", base.handshake_timeout)),
            msg_rate=float(limits.get("msg_rate", base.msg_rate)),
            msg_burst=int(limits.get("msg_burst", base.msg_burst)),
            scenarios_dir=str(maps.get("scenarios_dir", base.scenarios_dir)),
            allow_random=bool(maps.get("allow_random", base.allow_random)),
            log_level=str(logging_.get("level", base.log_level)),
            log_file=str(logging_.get("file", base.log_file)),
        )


def load_config(path: str | Path | None) -> ServerConfig:
    """Carga `server.toml` desde `path`. Sin `path`, devuelve los defaults.

    Lanza `FileNotFoundError` si `path` se indicó pero no existe, y
    `tomllib.TOMLDecodeError` (subclase de `ValueError`) si el TOML es inválido.
    """
    if path is None:
        return ServerConfig()
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"No existe el archivo de configuración: {p}")
    with p.open("rb") as fh:
        data = tomllib.load(fh)
    return ServerConfig.from_toml(data)
