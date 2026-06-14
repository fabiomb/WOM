"""Genera el icono del ejecutable a partir de data/assets/icon.png.

PyInstaller embebe el icono del .exe como recurso ICO de Windows (un PNG no
sirve directo, salvo que Pillow esté instalado y haga la conversión al
compilar). Para que el build no dependa de Pillow, este script genera de
antemano los formatos que cada plataforma necesita:

- `icon.ico`  (Windows): multi-resolución 16..256 px.
- `icon.icns` (macOS):  multi-resolución 16..1024 px.

Linux no embebe icono en el ejecutable (se ignora el parámetro), así que no
hace falta generar nada para esa plataforma.

Uso (requiere Pillow, solo en tiempo de desarrollo):
    python tools/gen_icon.py
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image

ASSETS_DIR = Path(__file__).resolve().parents[1] / "data" / "assets"
SOURCE = ASSETS_DIR / "icon.png"

# Tamaños estándar de un ICO de Windows (256 es el máximo por frame).
ICO_SIZES = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
# Tamaños de un ICNS de macOS.
ICNS_SIZES = [16, 32, 64, 128, 256, 512, 1024]


def main() -> None:
    if not SOURCE.exists():
        raise SystemExit(f"No existe {SOURCE}; agregá el PNG del icono primero.")
    img = Image.open(SOURCE).convert("RGBA")

    ico_path = ASSETS_DIR / "icon.ico"
    img.save(ico_path, format="ICO", sizes=ICO_SIZES)
    print(f"escrito {ico_path} ({len(ICO_SIZES)} resoluciones)")

    icns_path = ASSETS_DIR / "icon.icns"
    icns_source = img.resize((1024, 1024), Image.LANCZOS)
    try:
        icns_source.save(icns_path, format="ICNS", sizes=[(s, s) for s in ICNS_SIZES])
        print(f"escrito {icns_path} ({len(ICNS_SIZES)} resoluciones)")
    except (OSError, ValueError) as exc:  # el writer ICNS no está en todos los SO
        print(f"ICNS omitido ({exc}); generalo en macOS si hace falta el icono .app")


if __name__ == "__main__":
    main()
