"""Diálogo nativo del sistema operativo para elegir un archivo.

pygame no trae selector de archivos, así que se usa `tkinter` (stdlib). El
import de tkinter es **perezoso** (dentro de la función): si el entorno no lo
tiene (build headless, sin Tk), `pick_image` devuelve None y la UI cae al
ingreso manual de la ruta. El diálogo es modal y bloquea hasta que el usuario
elige o cancela; al volver, pygame sigue normalmente.
"""

from __future__ import annotations

# Filtros del diálogo: pares (etiqueta, patrones separados por espacio).
IMAGE_TYPES = [
    ("Imágenes", "*.png *.jpg *.jpeg *.bmp *.gif"),
    ("Todos los archivos", "*.*"),
]


def pick_image(title: str = "Elegir imagen del escenario") -> str | None:
    """Abre el diálogo del SO para elegir una imagen.

    Devuelve la ruta absoluta elegida, o None si el usuario canceló o tkinter
    no está disponible.
    """
    try:
        import tkinter as tk
        from tkinter import filedialog
    except Exception:
        return None
    root = None
    try:
        root = tk.Tk()
        root.withdraw()  # sin ventana raíz: solo el diálogo
        root.attributes("-topmost", True)  # por encima de la ventana del juego
        path = filedialog.askopenfilename(title=title, filetypes=IMAGE_TYPES)
    except Exception:
        return None
    finally:
        if root is not None:
            try:
                root.destroy()
            except Exception:
                pass
    return path or None
