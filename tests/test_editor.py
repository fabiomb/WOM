"""Tests del editor de mapas (headless, driver dummy de SDL)."""

import os

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

import pygame
import pytest

from wom.core.worldmap import NEUTRAL, Terrain
from wom.persistence.scenario import load_scenario
from wom.ui import theme
from wom.ui.editor_screen import EditorScreen


@pytest.fixture(scope="module")
def screen():
    pygame.init()
    yield pygame.display.set_mode(theme.WINDOW_SIZE)
    pygame.quit()


@pytest.fixture()
def editor(screen) -> EditorScreen:
    return EditorScreen(size="chico")


def test_lienzo_inicial_es_pradera(editor):
    world = editor.game.world
    assert all(t is Terrain.PLAINS for row in world.tiles for t in row)
    assert world.forts == [] and world.towns == [] and editor.game.armies == []


def test_render_inicial(screen, editor):
    editor.draw(screen)  # no debe lanzar


def test_pintar_terreno(editor):
    editor.set_terrain((2, 3), Terrain.MOUNTAIN)
    assert editor.game.world.tiles[3][2] is Terrain.MOUNTAIN


def test_colocar_y_capturar_fuerte_con_dueno(editor):
    editor.owner = 1
    editor.place_site((4, 4), "fort")
    fort = editor.game.world.fort_at((4, 4))
    assert fort is not None and fort.owner == 1
    assert editor.game.world.tiles[4][4] is Terrain.PLAINS  # transitable


def test_colocar_pueblo_reemplaza_fuerte(editor):
    editor.owner = NEUTRAL
    editor.place_site((5, 5), "fort")
    editor.place_site((5, 5), "town")
    assert editor.game.world.fort_at((5, 5)) is None
    assert editor.game.world.town_at((5, 5)) is not None


def test_sembrar_ejercito(editor):
    editor.owner = 0
    editor.place_army((3, 3))
    army = editor.game.army_at((3, 3))
    assert army is not None and army.owner == 0 and army.total_troops > 0


def test_ejercito_neutral_cae_a_jugador1(editor):
    editor.owner = NEUTRAL
    editor.place_army((6, 6))
    assert editor.game.army_at((6, 6)).owner == 0


def test_borrar_limpia_todo(editor):
    editor.owner = 1
    editor.set_terrain((7, 7), Terrain.FOREST)
    editor.place_site((7, 7), "fort")  # fuerza pradera
    editor.place_army((7, 7))
    editor.erase_tile((7, 7))
    world = editor.game.world
    assert world.fort_at((7, 7)) is None
    assert editor.game.army_at((7, 7)) is None
    assert world.tiles[7][7] is Terrain.PLAINS


def test_nuevo_limpia_y_cambia_tamano(editor):
    editor.set_terrain((1, 1), Terrain.WATER)
    editor.new_blank("medio")
    world = editor.game.world
    from wom.ui.menu_screen import MAP_SIZES

    assert (world.width, world.height) == MAP_SIZES["medio"][:2]
    assert all(t is Terrain.PLAINS for row in world.tiles for t in row)


def test_generar_aleatorio_reemplaza(editor):
    editor._randomize()
    world = editor.game.world
    terrains = {t for row in world.tiles for t in row}
    assert len(world.forts) >= 2  # el generador siembra fuertes
    assert terrains != {Terrain.PLAINS}  # ya no es un lienzo en blanco


def test_guardar_y_recargar(editor, tmp_path, monkeypatch):
    import wom.persistence.scenario as scenario

    monkeypatch.setattr(scenario, "MAPS_DIR", tmp_path)
    editor.owner = 0
    editor.place_site((2, 2), "fort")
    editor.place_army((2, 2))
    editor.set_terrain((4, 1), Terrain.MOUNTAIN)

    from wom.ui.editor_screen import SaveForm

    form = SaveForm()
    form.fields["title"] = "Mi escenario"
    form.ai_level = "dificil"
    editor._apply_form_and_save(form)

    saved = list(tmp_path.glob("*.wom"))
    assert len(saved) == 1
    assert editor.current_path == saved[0]  # queda asociado al archivo
    doc = load_scenario(saved[0])
    assert doc.title == "Mi escenario"
    assert doc.ai_level == "dificil"
    assert doc.world.fort_at((2, 2)) is not None
    assert doc.world.tiles[1][4] is Terrain.MOUNTAIN
    assert any(tuple(s["position"]) == (2, 2) for s in doc.army_specs)


def test_guardar_sobreescribe_el_mismo_archivo(editor, tmp_path, monkeypatch):
    """Tras el primer guardado, "Guardar" pisa el archivo (no crea uno nuevo)."""
    import wom.persistence.scenario as scenario

    monkeypatch.setattr(scenario, "MAPS_DIR", tmp_path)
    from wom.ui.editor_screen import SaveForm

    form = SaveForm()
    form.fields["title"] = "Mapa"
    editor._apply_form_and_save(form)
    first = editor.current_path
    assert len(list(tmp_path.glob("*.wom"))) == 1

    # Editar y volver a guardar (lo que hace el botón "Guardar" con un archivo
    # ya asociado): mismo archivo, contenido actualizado.
    editor.set_terrain((5, 5), Terrain.WATER)
    editor._save_to(editor.current_path)
    assert editor.current_path == first
    assert len(list(tmp_path.glob("*.wom"))) == 1
    assert load_scenario(first).world.tiles[5][5] is Terrain.WATER


def test_nuevo_resetea_el_archivo_asociado(editor, tmp_path, monkeypatch):
    import wom.persistence.scenario as scenario

    monkeypatch.setattr(scenario, "MAPS_DIR", tmp_path)
    from wom.ui.editor_screen import SaveForm

    form = SaveForm()
    form.fields["title"] = "X"
    editor._apply_form_and_save(form)
    assert editor.current_path is not None
    editor.new_blank("chico")
    assert editor.current_path is None
    assert editor.doc_meta["title"] == ""


def test_editar_metadata_sin_guardar(editor, tmp_path, monkeypatch):
    """El botón "Datos del escenario" actualiza la metadata en edición pero no
    escribe ningún archivo."""
    import wom.persistence.scenario as scenario
    from wom.ui.editor_screen import SaveForm

    monkeypatch.setattr(scenario, "MAPS_DIR", tmp_path)
    form = SaveForm()
    form.fields["title"] = "Sin guardar"
    form.fields["description"] = "Solo metadata"
    editor._form_mode = "meta"
    editor._apply_form_meta(form)

    assert editor.doc_meta["title"] == "Sin guardar"
    assert editor.doc_meta["description"] == "Solo metadata"
    assert editor.current_path is None  # no se asoció ningún archivo
    assert list(tmp_path.glob("*.wom")) == []  # ni se guardó


def test_metadata_se_conserva_al_guardar(editor, tmp_path, monkeypatch):
    """La metadata editada antes de guardar viaja al `.wom`."""
    import wom.persistence.scenario as scenario
    from wom.ui.editor_screen import SaveForm

    monkeypatch.setattr(scenario, "MAPS_DIR", tmp_path)
    form = SaveForm()
    form.fields["title"] = "Editado en vivo"
    form.ai_level = "dificil"
    editor._apply_form_meta(form)  # solo metadata, sin guardar

    editor._save_to(None)  # ahora guarda con la metadata ya cargada
    saved = list(tmp_path.glob("*.wom"))
    assert len(saved) == 1
    doc = load_scenario(saved[0])
    assert doc.title == "Editado en vivo"
    assert doc.ai_level == "dificil"


def test_boton_metadata_abre_form_en_modo_meta(screen, editor):
    editor.draw(screen)
    btn = editor._buttons["metadata"]
    editor.handle_event(
        pygame.event.Event(pygame.MOUSEBUTTONDOWN, {"pos": btn.center, "button": 1})
    )
    assert editor.save_form is not None
    assert editor._form_mode == "meta"
    editor.draw(screen)  # el formulario no debe romper el dibujo


def test_elegir_imagen_con_dialogo_la_importa(editor, tmp_path, monkeypatch):
    """El botón "Elegir imagen…" usa el diálogo del SO; la imagen elegida se
    importa (bytes) y queda incrustada dentro del `.wom` al guardar."""
    import wom.persistence.scenario as scenario
    from wom.ui import filedialog
    from wom.ui.editor_screen import SaveForm

    monkeypatch.setattr(scenario, "MAPS_DIR", tmp_path)
    img = pygame.Surface((10, 8))
    img.fill((200, 30, 30))
    img_path = tmp_path / "portada.png"
    pygame.image.save(img, str(img_path))

    form = SaveForm()
    form.fields["title"] = "Con imagen"
    monkeypatch.setattr(filedialog, "pick_image", lambda *a, **k: str(img_path))
    form._browse_image()  # equivale a clickear "Elegir imagen…"
    assert form.fields["image_path"] == str(img_path)
    assert form.preview is not None  # miniatura cargada

    editor._apply_form_and_save(form)
    saved = list(tmp_path.glob("*.wom"))
    assert len(saved) == 1
    doc = load_scenario(saved[0])
    assert doc.image_bytes is not None  # la imagen viajó dentro del `.wom`


def test_elegir_imagen_cancelado_no_cambia_nada(monkeypatch):
    from wom.ui import filedialog
    from wom.ui.editor_screen import SaveForm

    monkeypatch.setattr(filedialog, "pick_image", lambda *a, **k: None)
    form = SaveForm()
    form._browse_image()
    assert form.fields["image_path"] == "" and form.preview is None


def test_form_con_imagen_existente_muestra_miniatura(screen, tmp_path):
    """Al abrir el form de un documento con imagen, hay miniatura y aviso."""
    from wom.ui.editor_screen import SaveForm

    img = pygame.Surface((12, 9))
    img.fill((40, 80, 200))
    raw = (tmp_path / "x.png")
    pygame.image.save(img, str(raw))
    data = raw.read_bytes()
    form = SaveForm(image_bytes=data)
    assert form.has_image and form.preview is not None
    form.draw(screen)  # la fila de imagen con miniatura no debe romper


def test_boton_elegir_imagen_desde_el_editor(screen, editor, tmp_path, monkeypatch):
    """Flujo completo por la UI: Datos del escenario → Elegir imagen…"""
    from wom.ui import filedialog

    img = pygame.Surface((6, 6))
    img.fill((10, 200, 10))
    p = tmp_path / "i.png"
    pygame.image.save(img, str(p))
    monkeypatch.setattr(filedialog, "pick_image", lambda *a, **k: str(p))

    editor.draw(screen)
    meta_btn = editor._buttons["metadata"]
    editor.handle_event(
        pygame.event.Event(pygame.MOUSEBUTTONDOWN, {"pos": meta_btn.center, "button": 1})
    )
    editor.draw(screen)  # dibuja el form y registra el botón "Elegir imagen…"
    browse = editor.save_form._buttons["browse_image"]
    editor.handle_event(
        pygame.event.Event(pygame.MOUSEBUTTONDOWN, {"pos": browse.center, "button": 1})
    )
    assert editor.save_form.fields["image_path"] == str(p)


def test_refresh_terrain_autotila(editor):
    # Pintar agua aislada y verificar que el renderer la registró como costa.
    editor.set_terrain((3, 3), Terrain.WATER)
    assert (3, 3) in editor.renderer._water_tiles
