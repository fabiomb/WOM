# -*- mode: python ; coding: utf-8 -*-
"""Build distribuible de WOM (PyInstaller, modo onedir).

    pyinstaller wom.spec

Genera dist/wom/ con el ejecutable y data/ (config, assets, música) dentro
del bundle. saves/ y settings.json se crean junto al ejecutable al jugar
(app portable, ver wom/paths.py). Vale para Windows, Linux y macOS (cada
build se hace en su propio sistema operativo: PyInstaller no cruza
plataformas).

El icono del ejecutable sale de data/assets/icon.ico (Windows) o
icon.icns (macOS), pre-generados con tools/gen_icon.py para no depender de
Pillow al compilar. Linux no embebe icono en el ejecutable.
"""

import os
import sys

_ASSETS = os.path.join("data", "assets")
if sys.platform == "darwin":
    _icon = os.path.join(_ASSETS, "icon.icns")
elif sys.platform == "win32":
    _icon = os.path.join(_ASSETS, "icon.ico")
else:
    _icon = None
# Fallback al PNG (PyInstaller lo convierte si Pillow está disponible).
if _icon is None or not os.path.exists(_icon):
    _png = os.path.join(_ASSETS, "icon.png")
    _icon = _png if os.path.exists(_png) else None

a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=[],
    datas=[("data", "data")],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["pytest"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="wom",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,  # juego con ventana; sin consola de fondo en Windows
    disable_windowed_traceback=False,
    icon=_icon,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="wom",
)
