# -*- mode: python ; coding: utf-8 -*-
"""Build distribuible de WOM (PyInstaller, modo onedir).

    pyinstaller wom.spec

Genera dist/wom/ con el ejecutable y data/ (config, assets, música) dentro
del bundle. saves/ y settings.json se crean junto al ejecutable al jugar
(app portable, ver wom/paths.py). Vale para Windows, Linux y macOS (cada
build se hace en su propio sistema operativo: PyInstaller no cruza
plataformas).
"""

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
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="wom",
)
