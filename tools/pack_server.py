"""Arma el paquete mínimo y autosuficiente del servidor dedicado.

Copia SOLO lo que el servidor necesita —las capas puras de `wom/` (sin `ui/`,
sin pygame), la config de balance, los escenarios y la carpeta `server/`— a una
carpeta lista para instalar en un host. Ver `docs/server.md` §11.

Uso (desde la raíz del repo, con PYTHONPATH apuntando a la raíz no hace falta:
este script no importa `wom`):

    python tools/pack_server.py --out dist/wom-server

Resultado:

    dist/wom-server/
    ├── wom/        (core, net, persistence, ai + __init__.py, paths.py)
    ├── data/       (config/, scenarios/)
    ├── server/     (main.py, config.py, server.toml, README.md)
    ├── server.toml (copia de ejemplo a editar)
    └── README.md
"""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Subpaquetes puros de wom/ que el servidor usa (mapgen vive en core).
WOM_SUBPACKAGES = ("core", "net", "persistence", "ai")
# Módulos sueltos de wom/ necesarios (raíz del paquete).
WOM_TOP_MODULES = ("__init__.py", "paths.py")
# Datos de solo lectura que necesita.
DATA_DIRS = ("config", "scenarios")

_IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo")


def _copytree(src: Path, dst: Path) -> None:
    shutil.copytree(src, dst, ignore=_IGNORE, dirs_exist_ok=True)


def pack(out: Path) -> None:
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)

    # wom/ (solo lo puro)
    wom_out = out / "wom"
    wom_out.mkdir()
    for name in WOM_TOP_MODULES:
        shutil.copy2(ROOT / "wom" / name, wom_out / name)
    for pkg in WOM_SUBPACKAGES:
        _copytree(ROOT / "wom" / pkg, wom_out / pkg)

    # data/ (config + scenarios)
    for name in DATA_DIRS:
        src = ROOT / "data" / name
        if src.exists():
            _copytree(src, out / "data" / name)

    # server/
    _copytree(ROOT / "server", out / "server")

    # server.toml de ejemplo en la raíz del paquete + README breve
    shutil.copy2(ROOT / "server" / "server.toml", out / "server.toml")
    (out / "README.md").write_text(
        "# Servidor WOM (paquete mínimo)\n\n"
        "Paquete autosuficiente del servidor dedicado, armado con "
        "`tools/pack_server.py`.\n\n"
        "Arrancar:\n\n"
        "    python -m server --config server.toml --check   # valida\n"
        "    python -m server --config server.toml           # sirve\n\n"
        "Instalación, systemd y firewall: ver `docs/server.md` del repo.\n",
        encoding="utf-8",
    )

    _report(out)


def _report(out: Path) -> None:
    n_files = sum(1 for _ in out.rglob("*") if _.is_file())
    print(f"Paquete del servidor armado en: {out}")
    print(f"  {n_files} archivos. Probalo con:")
    print(f"    cd {out} && python -m server --config server.toml --check")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Arma el paquete del servidor de WOM")
    parser.add_argument(
        "--out",
        default=str(ROOT / "dist" / "wom-server"),
        help="carpeta de salida (por defecto dist/wom-server)",
    )
    args = parser.parse_args(argv)
    pack(Path(args.out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
