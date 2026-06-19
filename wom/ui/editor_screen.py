"""Editor de mapas y escenarios: dibujar terreno, sitios y tropas, y guardar
un `.wom` compartible.

La pantalla mantiene un `Game` interno como buffer de edición (un mundo de
pura pradera al empezar) y **reutiliza `MapRenderer`** para dibujar el lienzo
con el mismo aspecto que la partida (autotiling de agua incluido). La sidebar
es una paleta tipo editor gráfico: terreno (los 6 `Terrain`), sitios (fuerte,
pueblo), tropas (ejército) y borrador, todos aplicados con el **dueño** activo
(Neutral / Jugador 1 / Jugador 2). El menú ofrece Nuevo, Guardar, Cargar,
Generar aleatorio y Volver.

Guardar abre un formulario (título, descripción, modo de victoria, nivel de IA
del rival e imagen opcional) y empaqueta el `.wom` en `maps/`
(`wom/persistence/scenario.py`). Como otras pantallas, expone `wants_menu` para
que el loop de la app vuelva al menú, y `capturing_text` para que los atajos
globales (la M del reproductor) no roben las teclas mientras se escribe.
"""

from __future__ import annotations

import io
from pathlib import Path

import pygame

from wom.core.config import load_game_config
from wom.core.game import Game, Player
from wom.core.mapgen import MapParams, generate_map
from wom.core.victory import VictoryMode
from wom.core.worldmap import MAX_PLAYERS, NEUTRAL, Fort, Terrain, Town, WorldMap
from wom.persistence.scenario import (
    ScenarioDoc,
    list_maps,
    load_scenario,
    save_scenario,
    scenario_info,
)
from wom.ui import filedialog, scale, theme
from wom.ui.assets import Assets
from wom.ui.dialogs import TroopPicker
from wom.ui.menu_screen import AI_LEVELS, MAP_SIZES, VICTORY_LABELS, VICTORY_MODES
from wom.ui.renderer import MapRenderer

# Herramientas de terreno: (Terrain, etiqueta corta) en el orden de la paleta.
TERRAIN_TOOLS: list[tuple[Terrain, str]] = [
    (Terrain.PLAINS, "Pradera"),
    (Terrain.FOREST, "Bosque"),
    (Terrain.MOUNTAIN, "Montaña"),
    (Terrain.WATER, "Agua"),
    (Terrain.BRIDGE_H, "Puente E-O"),
    (Terrain.BRIDGE_V, "Puente N-S"),
]
OWNERS = [NEUTRAL, *range(MAX_PLAYERS)]
OWNER_LABELS = {NEUTRAL: "Neutral", **{i: f"Jugador {i + 1}" for i in range(MAX_PLAYERS)}}
SWATCH = 60  # lado de cada miniatura de la paleta


def _editor_players() -> list[Player]:
    """Jugadores del buffer de edición: el humano más rivales IA hasta el tope,
    para que la paleta admita cualquier dueño 0..MAX_PLAYERS-1."""
    return [Player(0, "Jugador")] + [
        Player(i, f"Rival {i}", is_ai=True, ai_level="medio") for i in range(1, MAX_PLAYERS)
    ]


def _blank_game(width: int, height: int) -> Game:
    """Buffer de edición: mundo de pura pradera, sin sitios ni tropas."""
    world = WorldMap(
        width=width,
        height=height,
        tiles=[[Terrain.PLAINS] * width for _ in range(height)],
    )
    return Game.from_setup(world, _editor_players(), [], VictoryMode.TOTAL)


class EditorScreen:
    def __init__(self, size: str = "medio"):
        self.config = load_game_config()
        self.size = size if size in MAP_SIZES else "medio"
        width, height, _, _ = MAP_SIZES[self.size]
        self.game = _blank_game(width, height)
        self.tool = f"terrain:{Terrain.PLAINS.value}"
        self.owner = 0
        self.wants_menu = False
        self.notice: str | None = None
        self.notice_until = 0
        self._painting = False
        self._grab_anchor: tuple[int, int] | None = None
        self._last_frame_ticks = 0
        self._buttons: dict[str, pygame.Rect] = {}
        self._dialog_buttons: dict[str, pygame.Rect] = {}

        # Modales (a lo sumo uno activo): formulario de guardado, lista de
        # carga/tamaño, selector de tropas, confirmación sí/no.
        self.save_form: SaveForm | None = None
        # Qué hace el formulario al aceptar: "save" guarda en disco, "meta" solo
        # actualiza la metadata en edición (título/descripción/imagen) sin guardar.
        self._form_mode = "save"
        self.list_modal: ListModal | None = None
        self._list_kind: str | None = None
        self.army_picker: TroopPicker | None = None
        self._army_tile: tuple[int, int] | None = None
        self._confirm: tuple[str, str, str, callable] | None = None

        # Documento en edición: archivo asociado (None hasta el primer guardado)
        # y su metadata, para que "Guardar" sobreescriba sin volver a pedir
        # datos. La imagen se guarda como bytes (al elegirla en el formulario o
        # al cargar un `.wom`).
        self.current_path: Path | None = None
        self.doc_meta: dict = {
            "title": "",
            "description": "",
            "victory_mode": VictoryMode.TOTAL,
            "ai_level": "medio",
        }
        self.doc_image: bytes | None = None

        self.font = pygame.font.SysFont(None, 30)
        self.small_font = pygame.font.SysFont(None, 22)
        self.map_area = pygame.Rect(
            0, 0, theme.WINDOW_SIZE[0] - theme.SIDEBAR_WIDTH, theme.WINDOW_SIZE[1]
        )
        self.sidebar = pygame.Rect(
            self.map_area.right, 0, theme.SIDEBAR_WIDTH, theme.WINDOW_SIZE[1]
        )
        self.swatch_assets = Assets(SWATCH)
        self.renderer: MapRenderer | None = None
        self._rebuild_renderer()

    @property
    def capturing_text(self) -> bool:
        return self.save_form is not None and self.save_form.capturing_text

    def _rebuild_renderer(self) -> None:
        """(Re)crea el renderer tras cambiar de mundo (nuevo/cargar/generar)."""
        tile_size = max(
            1,
            min(
                self.map_area.width // self.game.world.width,
                self.map_area.height // self.game.world.height,
            ),
        )
        self.renderer = MapRenderer(self.game, Assets(tile_size), self.map_area)

    # --- input -----------------------------------------------------------------

    def handle_event(self, event: pygame.event.Event) -> None:
        if self._confirm is not None:
            choice = self._confirm_choice(event)
            if choice is True:
                self._confirm[3]()
                self._confirm = None
            elif choice is False:
                self._confirm = None
            return
        if self.save_form is not None:
            result = self.save_form.handle_event(event)
            if result == "accept":
                if self._form_mode == "meta":
                    self._apply_form_meta(self.save_form)
                else:
                    self._apply_form_and_save(self.save_form)
                self.save_form = None
            elif result == "cancel":
                self.save_form = None
            return
        if self.list_modal is not None:
            result = self.list_modal.handle_event(event)
            if result is not None:
                self._resolve_list(result)
            return
        if self.army_picker is not None:
            choice = self.army_picker.handle_event(event)
            if choice == "accept":
                self._apply_army_picker()
            elif choice == "cancel":
                self.army_picker = None
                self._army_tile = None
            return

        if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
            self._confirm = (
                "¿Salir del editor?",
                "El mapa no guardado se pierde",
                "Salir (S)",
                self._leave,
            )
            return
        if event.type == pygame.MOUSEWHEEL:
            if event.y:
                self.renderer.zoom(event.y, scale.mouse_pos())
            return
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 2:
            self._grab_anchor = event.pos
            return
        if event.type == pygame.MOUSEBUTTONUP and event.button == 2:
            self._grab_anchor = None
            return
        if event.type == pygame.MOUSEMOTION and self._grab_anchor is not None:
            if not event.buttons[1]:
                self._grab_anchor = None
                return
            dx = event.pos[0] - self._grab_anchor[0]
            dy = event.pos[1] - self._grab_anchor[1]
            self.renderer.camera.pan(-dx, -dy)
            self._grab_anchor = event.pos
            return
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            if self.sidebar.collidepoint(event.pos):
                self._click_sidebar(event.pos)
            else:
                self._painting = True
                self._paint_at(event.pos, dragging=False)
        elif event.type == pygame.MOUSEBUTTONUP and event.button == 1:
            self._painting = False
        elif event.type == pygame.MOUSEMOTION and self._painting:
            if event.buttons[0]:
                self._paint_at(event.pos, dragging=True)
            else:
                self._painting = False

    def _click_sidebar(self, point: tuple[int, int]) -> None:
        hit = next(
            (bid for bid, rect in self._buttons.items() if rect.collidepoint(point)),
            None,
        )
        if hit is None:
            return
        if hit == "owner":
            self.owner = OWNERS[(OWNERS.index(self.owner) + 1) % len(OWNERS)]
        elif hit.startswith("tool:"):
            self.tool = hit[len("tool:"):]
        elif hit == "new":
            self.list_modal = ListModal(
                "Nuevo mapa (se pierde lo actual)",
                [(key, f"{key}  ({w}x{h})") for key, (w, h, _, _) in MAP_SIZES.items()],
            )
            self._list_kind = "new"
        elif hit == "metadata":
            # Datos del escenario (título/descripción/victoria/IA/imagen) como
            # parte de la edición: el formulario solo actualiza la metadata, no
            # guarda en disco.
            self.save_form = SaveForm(
                self.doc_meta,
                heading="Datos del escenario",
                accept_label="Aplicar (Enter)",
                image_bytes=self.doc_image,
            )
            self._form_mode = "meta"
        elif hit == "save":
            # Guardar: sobreescribe el archivo actual; si todavía no se guardó
            # nunca, abre el formulario (equivale a "Guardar como").
            if self.current_path is not None:
                self._save_to(self.current_path)
            else:
                self.save_form = SaveForm(self.doc_meta, image_bytes=self.doc_image)
                self._form_mode = "save"
        elif hit == "save_as":
            self.save_form = SaveForm(  # siempre archivo nuevo
                self.doc_meta, image_bytes=self.doc_image
            )
            self._form_mode = "save"
        elif hit == "load":
            entries = [(str(p), p.stem) for p in list_maps()]
            self.list_modal = ListModal("Cargar mapa", entries, empty="No hay mapas")
            self._list_kind = "load"
        elif hit == "generate":
            self._confirm = (
                "¿Generar mapa aleatorio?",
                "Reemplaza todo el mapa actual",
                "Generar (S)",
                self._randomize,
            )
        elif hit == "back":
            self._confirm = (
                "¿Salir del editor?",
                "El mapa no guardado se pierde",
                "Salir (S)",
                self._leave,
            )

    def _paint_at(self, point: tuple[int, int], dragging: bool) -> None:
        tile = self.renderer.screen_to_tile(point, self.game)
        if tile is None:
            return
        if self.tool.startswith("terrain:"):
            terrain = Terrain(self.tool[len("terrain:"):])
            self.set_terrain(tile, terrain)
        elif dragging:
            return  # sitios y tropas solo se colocan con click, no en arrastre
        elif self.tool == "fort":
            self.place_site(tile, "fort")
        elif self.tool == "town":
            self.place_site(tile, "town")
        elif self.tool == "army":
            self._place_or_edit_army(tile)
        elif self.tool == "erase":
            self.erase_tile(tile)

    # --- operaciones de edición (mutan el buffer; testeables sin render) -------

    def set_terrain(self, tile: tuple[int, int], terrain: Terrain) -> None:
        x, y = tile
        self.game.world.tiles[y][x] = terrain
        self.renderer.refresh_terrain()

    def place_site(self, tile: tuple[int, int], kind: str) -> None:
        """Coloca/actualiza un fuerte o pueblo del dueño activo; el tile queda
        transitable (pradera) como exige el generador."""
        world = self.game.world
        x, y = tile
        world.forts = [f for f in world.forts if f.position != tile]
        world.towns = [t for t in world.towns if t.position != tile]
        if kind == "fort":
            world.forts.append(Fort(position=tile, owner=self.owner))
        else:
            world.towns.append(Town(position=tile, owner=self.owner))
        world.tiles[y][x] = Terrain.PLAINS
        self.renderer.refresh_terrain()

    def place_army(self, tile: tuple[int, int], composition: dict[str, int] | None = None) -> None:
        """Siembra (o reemplaza) un ejército del dueño activo en el tile.

        El dueño Neutral no aplica a tropas: cae a Jugador 1.
        """
        owner = self.owner if self.owner in (0, 1) else 0
        self.game.armies = [a for a in self.game.armies if a.position != tile]
        comp = composition or dict(self.config["ejercito_inicial"])
        self.game.spawn_army(owner, tile, dict(comp))

    def erase_tile(self, tile: tuple[int, int]) -> None:
        """Limpia el tile por completo: tropa, sitio y terreno a pradera."""
        world = self.game.world
        x, y = tile
        self.game.armies = [a for a in self.game.armies if a.position != tile]
        world.forts = [f for f in world.forts if f.position != tile]
        world.towns = [t for t in world.towns if t.position != tile]
        world.tiles[y][x] = Terrain.PLAINS
        self.renderer.refresh_terrain()

    def new_blank(self, size: str) -> None:
        self.size = size
        width, height, _, _ = MAP_SIZES[size]
        self.game = _blank_game(width, height)
        self._rebuild_renderer()
        # Documento nuevo, sin archivo: el próximo "Guardar" pedirá los datos.
        self.current_path = None
        self.doc_meta = {
            "title": "",
            "description": "",
            "victory_mode": VictoryMode.TOTAL,
            "ai_level": "medio",
        }
        self.doc_image = None

    def _randomize(self) -> None:
        width, height, forts, towns = MAP_SIZES[self.size]
        params = MapParams(width, height, forts, towns, seed=None)
        world = generate_map(params, self.game.rng)
        self.game = Game.from_setup(world, _editor_players(), [], self.game.victory_mode)
        self._rebuild_renderer()
        self._notify("Mapa aleatorio generado")

    # --- modal de tropas -------------------------------------------------------

    def _place_or_edit_army(self, tile: tuple[int, int]) -> None:
        """Click con la herramienta de tropas: si hay un ejército, edita su
        composición; si no, lo siembra con la composición default."""
        army = self.game.army_at(tile)
        if army is None:
            self.place_army(tile)
            return
        rows = [
            (cid, self.game.classes[cid].nombre, self.config["max_army_size"])
            for cid in sorted(self.game.classes)
        ]
        self._army_tile = tile
        self.army_picker = TroopPicker(
            "Tropas del ejército",
            f"En {tile} · dueño {OWNER_LABELS[army.owner]}",
            rows,
            cap=self.config["max_army_size"],
            initial=dict(army.composition),
            accept_label="Aplicar (S)",
        )

    def _apply_army_picker(self) -> None:
        amounts = {c: n for c, n in self.army_picker.amounts.items() if n > 0}
        tile = self._army_tile
        self.army_picker = None
        self._army_tile = None
        if not amounts:
            self.game.armies = [a for a in self.game.armies if a.position != tile]
            return
        army = self.game.army_at(tile)
        if army is not None:
            army.composition = amounts

    # --- modales de lista / guardado / confirmación ----------------------------

    def _resolve_list(self, result: str) -> None:
        kind, self._list_kind = self._list_kind, None
        modal, self.list_modal = self.list_modal, None
        if result == "__cancel__":
            return
        if kind == "new":
            self.new_blank(result)
        elif kind == "load":
            try:
                doc = load_scenario(Path(result))
            except (OSError, ValueError, KeyError) as exc:
                self._notify(f"No se pudo cargar: {exc}")
                return
            self.game = Game.from_setup(
                doc.world, doc.players, doc.army_specs, doc.victory_mode
            )
            self.size = self._closest_size(doc.world)
            self._rebuild_renderer()
            # Pasa a editar este archivo: el próximo "Guardar" lo sobreescribe.
            self.current_path = Path(result)
            self.doc_meta = {
                "title": doc.title,
                "description": doc.description,
                "victory_mode": doc.victory_mode,
                "ai_level": doc.ai_level,
            }
            self.doc_image = doc.image_bytes
            self._notify(f"Cargado: {Path(result).stem}")

    def _closest_size(self, world: WorldMap) -> str:
        """El preset cuyas dimensiones más se acercan al mapa cargado (solo
        afecta qué tamaño usaría 'Generar aleatorio' después)."""
        return min(
            MAP_SIZES,
            key=lambda k: abs(MAP_SIZES[k][0] - world.width)
            + abs(MAP_SIZES[k][1] - world.height),
        )

    def _read_form_meta(self, form: "SaveForm") -> None:
        """Vuelca los datos del formulario en la metadata del documento. La
        imagen (si se indicó una ruta nueva) se lee a bytes; si no, se conserva
        la que ya tuviera el documento."""
        self.doc_meta = {
            "title": form.fields["title"].strip(),
            "description": form.fields["description"].strip(),
            "victory_mode": form.victory_mode,
            "ai_level": form.ai_level,
        }
        image_path = form.fields["image_path"].strip()
        if image_path:
            try:
                self.doc_image = Path(image_path).read_bytes()
            except OSError:
                self._notify("No se pudo leer la imagen")

    def _apply_form_meta(self, form: "SaveForm") -> None:
        """Edita la metadata del documento sin guardar (botón "Datos del
        escenario"): título/descripción/victoria/IA/imagen se actualizan en el
        buffer y el próximo "Guardar" los persiste."""
        self._read_form_meta(form)
        self._notify("Datos del escenario actualizados")

    def _apply_form_and_save(self, form: "SaveForm") -> None:
        """Toma los datos del formulario en la metadata del documento y guarda
        en un archivo nuevo (derivado del título). Lo usan el primer "Guardar"
        y "Guardar como"."""
        self._read_form_meta(form)
        self._save_to(None)

    def _build_doc(self) -> ScenarioDoc:
        """Documento `.wom` a partir del mapa actual y la metadata guardada.

        La cantidad de jugadores la fijan los dueños presentes en el mapa (el
        mayor id usado en fuertes/tropas, mínimo 2): así un mapa de 2 bandos
        guarda 2 jugadores y uno de 4, cuatro.
        """
        owners = {f.owner for f in self.game.world.forts if f.owner >= 0}
        owners |= {a.owner for a in self.game.armies if a.owner >= 0}
        n_players = max(max(owners) + 1, 2) if owners else 2
        players = [Player(0, "Jugador")] + [
            Player(i, f"Rival {i}", is_ai=True, ai_level=self.doc_meta["ai_level"])
            for i in range(1, n_players)
        ]
        return ScenarioDoc(
            world=self.game.world,
            players=players,
            army_specs=[
                {
                    "owner": a.owner,
                    "position": list(a.position),
                    "composition": dict(a.composition),
                }
                for a in self.game.armies
            ],
            title=self.doc_meta["title"],
            description=self.doc_meta["description"],
            victory_mode=self.doc_meta["victory_mode"],
            ai_level=self.doc_meta["ai_level"],
            image_bytes=self.doc_image,
        )

    def _save_to(self, path: Path | None) -> None:
        """Guarda el documento. Con `path` sobreescribe ese archivo; con None
        crea uno nuevo en `maps/` (nombre derivado del título). Recuerda el
        archivo para que el próximo "Guardar" lo sobreescriba."""
        doc = self._build_doc()
        try:
            if path is not None:
                saved = save_scenario(doc, name=path.stem, directory=path.parent)
            else:
                saved = save_scenario(doc)
        except OSError as exc:
            self._notify(f"No se pudo guardar: {exc}")
            return
        self.current_path = saved
        self._notify(f"Guardado: {saved.name}")

    def _leave(self) -> None:
        self.wants_menu = True

    def _confirm_choice(self, event: pygame.event.Event) -> bool | None:
        if event.type == pygame.KEYDOWN:
            if event.key in (pygame.K_s, pygame.K_RETURN, pygame.K_KP_ENTER):
                return True
            if event.key in (pygame.K_n, pygame.K_ESCAPE):
                return False
        elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            yes = self._dialog_buttons.get("yes")
            no = self._dialog_buttons.get("no")
            if yes is not None and yes.collidepoint(event.pos):
                return True
            if no is not None and no.collidepoint(event.pos):
                return False
        return None

    def _notify(self, text: str) -> None:
        self.notice = text
        self.notice_until = pygame.time.get_ticks() + 3000

    # --- dibujo ----------------------------------------------------------------

    def draw(self, surface: pygame.Surface) -> None:
        surface.fill(theme.BACKGROUND)
        self._update_camera()
        surface.set_clip(self.renderer.area)
        self.renderer.draw(surface, self.game, None, {})
        surface.set_clip(None)
        self._draw_sidebar(surface)
        if self._confirm is not None:
            self._draw_confirm(surface, *self._confirm[:3])
        elif self.save_form is not None:
            self.save_form.draw(surface)
        elif self.list_modal is not None:
            self.list_modal.draw(surface)
        elif self.army_picker is not None:
            self.army_picker.draw(surface)

    def _update_camera(self) -> None:
        now = pygame.time.get_ticks()
        dt = min((now - self._last_frame_ticks) / 1000.0, 0.1)
        self._last_frame_ticks = now
        blocked = (
            self._confirm is not None
            or self.save_form is not None
            or self.list_modal is not None
            or self.army_picker is not None
            or self._grab_anchor is not None
            or self._painting
        )
        if not blocked:
            self.renderer.camera.edge_pan(
                scale.mouse_pos(), dt, (0, 0, *theme.WINDOW_SIZE)
            )

    def _draw_sidebar(self, surface: pygame.Surface) -> None:
        self._buttons.clear()
        pygame.draw.rect(surface, theme.SIDEBAR_BG, self.sidebar)
        x = self.sidebar.x + 20
        y = 18
        title = self.font.render("Editor de mapas", True, theme.TEXT)
        surface.blit(title, (x, y))
        y += 38
        # Archivo en edición: el nombre del `.wom` o "(sin guardar)".
        editing = self.current_path.stem if self.current_path is not None else "(sin guardar)"
        name = self.small_font.render(f"Editando: {editing}", True, theme.TEXT_DIM)
        surface.blit(name, (x, y))
        y += 24
        # Metadata en edición (la edita el botón "Datos del escenario").
        title = self.doc_meta["title"].strip() or "(sin título)"
        img_mark = " · con imagen" if self.doc_image is not None else ""
        meta = self.small_font.render(f"Título: {title}{img_mark}", True, theme.TEXT_DIM)
        surface.blit(meta, (x, y))
        y += 28

        y = self._sidebar_button(surface, "owner", f"Dueño: {OWNER_LABELS[self.owner]}", x, y)
        y += 6

        y = self._palette_label(surface, "Terreno", x, y)
        terrain_swatches = [
            (f"terrain:{t.value}", self.swatch_assets.terrain[t], label)
            for t, label in TERRAIN_TOOLS
        ]
        y = self._swatch_grid(surface, terrain_swatches, x, y)

        y = self._palette_label(surface, "Sitios y tropas", x, y)
        site_swatches = [
            ("fort", self.swatch_assets.icons["fort"], "Fuerte"),
            ("town", self.swatch_assets.icons["town"], "Pueblo"),
            ("army", self.swatch_assets.units["soldado"], "Ejército"),
            ("erase", None, "Borrar"),
        ]
        y = self._swatch_grid(surface, site_swatches, x, y)
        y += 8

        for bid, label in (
            ("new", "Nuevo"),
            ("metadata", "Datos del escenario"),
            ("save", "Guardar"),
            ("save_as", "Guardar como…"),
            ("load", "Cargar mapa"),
            ("generate", "Generar aleatorio"),
            ("back", "Volver al menú (ESC)"),
        ):
            y = self._sidebar_button(surface, bid, label, x, y)
        y += 4
        hint = self.small_font.render(
            "Click pinta · rueda zoom · botón medio arrastra", True, theme.TEXT_DIM
        )
        surface.blit(hint, (x, y))
        notice = self.notice if pygame.time.get_ticks() < self.notice_until else None
        if notice:
            label = self.small_font.render(notice, True, theme.SELECTION)
            surface.blit(label, (x, self.sidebar.bottom - 30))

    def _palette_label(self, surface, text, x, y) -> int:
        label = self.small_font.render(text, True, theme.TEXT_DIM)
        surface.blit(label, (x, y))
        return y + 26

    def _swatch_grid(self, surface, items, x, y) -> int:
        """Miniaturas de la paleta en filas; resalta la herramienta activa."""
        per_row = 4
        gap = 12
        for i, (tool_id, sprite, label) in enumerate(items):
            col = i % per_row
            if col == 0 and i > 0:
                y += SWATCH + 22
            sx = x + col * (SWATCH + gap)
            rect = pygame.Rect(sx, y, SWATCH, SWATCH)
            pygame.draw.rect(surface, (50, 56, 62), rect, border_radius=6)
            if sprite is not None:
                surface.blit(sprite, sprite.get_rect(center=rect.center))
            else:  # borrador: una X
                pygame.draw.line(surface, theme.TEXT, rect.topleft, rect.bottomright, 4)
                pygame.draw.line(surface, theme.TEXT, rect.topright, rect.bottomleft, 4)
            if self.tool == tool_id:
                pygame.draw.rect(surface, theme.SELECTION, rect, 3, border_radius=6)
            cap = self.small_font.render(label, True, theme.TEXT_DIM)
            surface.blit(cap, cap.get_rect(midtop=(rect.centerx, rect.bottom + 2)))
            self._buttons[f"tool:{tool_id}"] = rect
        return y + SWATCH + 28

    def _sidebar_button(self, surface, bid, label, x, y) -> int:
        rect = pygame.Rect(x, y, self.sidebar.width - 40, 40)
        over = rect.collidepoint(scale.mouse_pos())
        pygame.draw.rect(
            surface, (70, 78, 86) if over else (50, 56, 62), rect, border_radius=6
        )
        text = self.small_font.render(label, True, theme.TEXT)
        surface.blit(text, text.get_rect(center=rect.center))
        self._buttons[bid] = rect
        return rect.bottom + 10

    def _draw_confirm(self, surface, title, detail, yes_label) -> None:
        overlay = pygame.Surface(surface.get_size(), pygame.SRCALPHA)
        overlay.fill(theme.GAMEOVER_BG)
        surface.blit(overlay, (0, 0))
        box = pygame.Rect(0, 0, 460, 150)
        box.center = surface.get_rect().center
        pygame.draw.rect(surface, theme.SIDEBAR_BG, box, border_radius=8)
        pygame.draw.rect(surface, theme.SELECTION, box, 2, border_radius=8)
        title_text = self.font.render(title, True, theme.TEXT)
        detail_text = self.small_font.render(detail, True, theme.TEXT_DIM)
        surface.blit(title_text, title_text.get_rect(midtop=(box.centerx, box.y + 18)))
        surface.blit(detail_text, detail_text.get_rect(midtop=(box.centerx, box.y + 50)))
        yes = pygame.Rect(box.x + 30, box.bottom - 56, 180, 38)
        no = pygame.Rect(box.right - 210, box.bottom - 56, 180, 38)
        mouse = scale.mouse_pos()
        for rect, text, base, hover in (
            (yes, yes_label, theme.BUTTON_BG, theme.BUTTON_BG_OVER),
            (no, "Cancelar (N)", (50, 56, 62), (70, 78, 86)),
        ):
            color = hover if rect.collidepoint(mouse) else base
            pygame.draw.rect(surface, color, rect, border_radius=6)
            label = self.font.render(text, True, theme.TEXT)
            surface.blit(label, label.get_rect(center=rect.center))
        self._dialog_buttons = {"yes": yes, "no": no}


class ListModal:
    """Lista modal de opciones (tamaño nuevo, mapas a cargar).

    `handle_event` devuelve el valor elegido, "__cancel__" o None.
    """

    def __init__(self, title: str, entries: list[tuple[str, str]], empty: str = ""):
        self.title = title
        self.entries = entries  # (valor, etiqueta)
        self.empty = empty
        self.font = pygame.font.SysFont(None, 30)
        self.small_font = pygame.font.SysFont(None, 22)
        self._buttons: dict[str, pygame.Rect] = {}

    def handle_event(self, event: pygame.event.Event) -> str | None:
        if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
            return "__cancel__"
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            for key, rect in self._buttons.items():
                if rect.collidepoint(event.pos):
                    return "__cancel__" if key == "cancel" else key
        return None

    def draw(self, surface: pygame.Surface) -> None:
        overlay = pygame.Surface(surface.get_size(), pygame.SRCALPHA)
        overlay.fill(theme.GAMEOVER_BG)
        surface.blit(overlay, (0, 0))
        rows = max(1, len(self.entries))
        box = pygame.Rect(0, 0, 560, 120 + min(rows, 10) * 46)
        box.center = surface.get_rect().center
        pygame.draw.rect(surface, theme.SIDEBAR_BG, box, border_radius=8)
        pygame.draw.rect(surface, theme.SELECTION, box, 2, border_radius=8)
        self._buttons = {}
        mouse = scale.mouse_pos()
        title = self.font.render(self.title, True, theme.TEXT)
        surface.blit(title, title.get_rect(midtop=(box.centerx, box.y + 16)))
        y = box.y + 58
        if not self.entries:
            label = self.small_font.render(self.empty, True, theme.TEXT_DIM)
            surface.blit(label, label.get_rect(center=(box.centerx, y + 10)))
            y += 46
        for value, text in self.entries[:10]:
            rect = pygame.Rect(box.x + 24, y, box.width - 48, 38)
            over = rect.collidepoint(mouse)
            pygame.draw.rect(
                surface, (70, 78, 86) if over else (50, 56, 62), rect, border_radius=6
            )
            label = self.small_font.render(text, True, theme.TEXT)
            surface.blit(label, label.get_rect(midleft=(rect.x + 12, rect.centery)))
            self._buttons[value] = rect
            y += 46
        cancel = pygame.Rect(box.centerx - 90, box.bottom - 50, 180, 38)
        over = cancel.collidepoint(mouse)
        pygame.draw.rect(
            surface, (70, 78, 86) if over else (50, 56, 62), cancel, border_radius=6
        )
        label = self.small_font.render("Cancelar (ESC)", True, theme.TEXT)
        surface.blit(label, label.get_rect(center=cancel.center))
        self._buttons["cancel"] = cancel


class SaveForm:
    """Formulario modal de guardado: título, descripción, victoria, IA, imagen.

    Título y descripción se editan escribiendo sobre el campo enfocado (click
    para enfocar); victoria y nivel de IA son cíclicos. La imagen se elige con
    el botón "Elegir imagen…" (diálogo nativo del SO vía `filedialog`), que solo
    completa la ruta del archivo a importar; la imagen se incrusta en el `.wom`
    recién al aplicar/guardar (no se referencia desde afuera). Una miniatura
    muestra la imagen actual o la recién elegida. `handle_event` devuelve
    "accept", "cancel" o None.
    """

    TEXT_FIELDS = [
        ("title", "Título"),
        ("description", "Descripción"),
    ]

    def __init__(
        self,
        meta: dict | None = None,
        heading: str = "Guardar mapa / escenario",
        accept_label: str = "Guardar (Enter)",
        image_bytes: bytes | None = None,
    ):
        meta = meta or {}
        self.heading = heading
        self.accept_label = accept_label
        # Imagen ya cargada en el documento (para la miniatura y el aviso). La
        # ruta del campo solo se usa al importar una imagen nueva.
        self.has_image = image_bytes is not None
        self.fields = {
            "title": meta.get("title", ""),
            "description": meta.get("description", ""),
            "image_path": "",  # ruta de una imagen nueva a importar (opcional)
        }
        self.victory_mode = meta.get("victory_mode", VictoryMode.TOTAL)
        self.ai_level = meta.get("ai_level", "medio")
        self.active: str | None = "title"
        self.font = pygame.font.SysFont(None, 30)
        self.small_font = pygame.font.SysFont(None, 22)
        self._buttons: dict[str, pygame.Rect] = {}
        self.preview = self._load_preview_bytes(image_bytes)

    @staticmethod
    def _load_preview_bytes(data: bytes | None) -> pygame.Surface | None:
        if not data:
            return None
        try:
            return pygame.image.load(io.BytesIO(data))
        except (pygame.error, OSError):
            return None

    def _browse_image(self) -> None:
        """Abre el diálogo del SO; si se elige un archivo, completa la ruta y
        actualiza la miniatura. Si tkinter no está, no pasa nada (queda el campo
        de texto manual)."""
        path = filedialog.pick_image()
        if not path:
            return
        self.fields["image_path"] = path
        self.active = None
        try:
            self.preview = pygame.image.load(path)
        except (pygame.error, OSError):
            self.preview = None

    @property
    def capturing_text(self) -> bool:
        return self.active is not None

    def handle_event(self, event: pygame.event.Event) -> str | None:
        if event.type == pygame.KEYDOWN:
            if event.key == pygame.K_ESCAPE:
                return "cancel"
            if self.active is not None:
                if event.key in (pygame.K_RETURN, pygame.K_KP_ENTER):
                    self.active = None  # un campo enfocado: Enter solo lo libera
                elif event.key == pygame.K_BACKSPACE:
                    self.fields[self.active] = self.fields[self.active][:-1]
                elif event.unicode and event.unicode.isprintable():
                    self.fields[self.active] += event.unicode
            elif event.key in (pygame.K_RETURN, pygame.K_KP_ENTER):
                return "accept"  # sin foco: Enter guarda
            return None
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            for key, rect in self._buttons.items():
                if not rect.collidepoint(event.pos):
                    continue
                if key == "accept":
                    return "accept"
                if key == "cancel":
                    return "cancel"
                if key == "browse_image":
                    self._browse_image()
                    return None
                if key == "victory":
                    self.victory_mode = VICTORY_MODES[
                        (VICTORY_MODES.index(self.victory_mode) + 1) % len(VICTORY_MODES)
                    ]
                elif key == "ai":
                    self.ai_level = AI_LEVELS[
                        (AI_LEVELS.index(self.ai_level) + 1) % len(AI_LEVELS)
                    ]
                elif key.startswith("field:"):
                    self.active = key.split(":", 1)[1]
                return None
            self.active = None  # click afuera de un campo: pierde foco
        return None

    def draw(self, surface: pygame.Surface) -> None:
        overlay = pygame.Surface(surface.get_size(), pygame.SRCALPHA)
        overlay.fill(theme.GAMEOVER_BG)
        surface.blit(overlay, (0, 0))
        box = pygame.Rect(0, 0, 640, 580)
        box.center = surface.get_rect().center
        pygame.draw.rect(surface, theme.SIDEBAR_BG, box, border_radius=8)
        pygame.draw.rect(surface, theme.SELECTION, box, 2, border_radius=8)
        self._buttons = {}
        mouse = scale.mouse_pos()
        title = self.font.render(self.heading, True, theme.TEXT)
        surface.blit(title, title.get_rect(midtop=(box.centerx, box.y + 16)))

        y = box.y + 60
        for key, label in self.TEXT_FIELDS:
            cap = self.small_font.render(label, True, theme.TEXT_DIM)
            surface.blit(cap, (box.x + 26, y))
            rect = pygame.Rect(box.x + 26, y + 22, box.width - 52, 36)
            focused = self.active == key
            pygame.draw.rect(surface, (24, 27, 30), rect, border_radius=6)
            pygame.draw.rect(
                surface, theme.SELECTION if focused else (70, 78, 86), rect, 2, border_radius=6
            )
            shown = self.fields[key] + ("_" if focused else "")
            text = self.small_font.render(shown, True, theme.TEXT)
            surface.blit(text, (rect.x + 8, rect.y + 8))
            self._buttons[f"field:{key}"] = rect
            y += 70

        y = self._draw_image_row(surface, box, y, mouse)

        for key, label in (
            ("victory", f"Victoria:  {VICTORY_LABELS[self.victory_mode]}"),
            ("ai", f"Nivel de IA rival:  {self.ai_level}"),
        ):
            rect = pygame.Rect(box.x + 26, y, box.width - 52, 36)
            over = rect.collidepoint(mouse)
            pygame.draw.rect(
                surface, (70, 78, 86) if over else (50, 56, 62), rect, border_radius=6
            )
            text = self.small_font.render(label, True, theme.TEXT)
            surface.blit(text, text.get_rect(midleft=(rect.x + 12, rect.centery)))
            self._buttons[key] = rect
            y += 46

        yes = pygame.Rect(box.x + 26, box.bottom - 52, 260, 38)
        no = pygame.Rect(box.right - 286, box.bottom - 52, 260, 38)
        for key, rect, label, base, hover in (
            ("accept", yes, self.accept_label, theme.BUTTON_BG, theme.BUTTON_BG_OVER),
            ("cancel", no, "Cancelar (ESC)", (50, 56, 62), (70, 78, 86)),
        ):
            color = hover if rect.collidepoint(mouse) else base
            pygame.draw.rect(surface, color, rect, border_radius=6)
            text = self.small_font.render(label, True, theme.TEXT)
            surface.blit(text, text.get_rect(center=rect.center))
            self._buttons[key] = rect

    def _draw_image_row(self, surface, box, y, mouse) -> int:
        """Fila de imagen: estado, ruta elegida, botón "Elegir imagen…" (diálogo
        del SO) y una miniatura de la imagen actual o la recién elegida."""
        if self.fields["image_path"].strip():
            label = "Imagen del escenario · nueva por importar"
        elif self.has_image:
            label = "Imagen del escenario · imagen cargada"
        else:
            label = "Imagen del escenario · (ninguna)"
        cap = self.small_font.render(label, True, theme.TEXT_DIM)
        surface.blit(cap, (box.x + 26, y))
        y += 22

        btn_w = 180
        field = pygame.Rect(box.x + 26, y, box.width - 52 - btn_w - 10, 36)
        focused = self.active == "image_path"
        pygame.draw.rect(surface, (24, 27, 30), field, border_radius=6)
        pygame.draw.rect(
            surface, theme.SELECTION if focused else (70, 78, 86), field, 2, border_radius=6
        )
        shown = self.fields["image_path"] + ("_" if focused else "")
        text = self.small_font.render(shown, True, theme.TEXT)
        clip = surface.get_clip()
        surface.set_clip(field.inflate(-12, 0))
        # Si la ruta excede el campo, se alinea a la derecha para ver el nombre.
        tx = field.x + 8
        if text.get_width() > field.width - 16:
            tx = field.right - 8 - text.get_width()
        surface.blit(text, (tx, field.y + 8))
        surface.set_clip(clip)
        self._buttons["field:image_path"] = field

        browse = pygame.Rect(field.right + 10, y, btn_w, 36)
        over = browse.collidepoint(mouse)
        pygame.draw.rect(
            surface, (70, 78, 86) if over else (50, 56, 62), browse, border_radius=6
        )
        blabel = self.small_font.render("Elegir imagen…", True, theme.TEXT)
        surface.blit(blabel, blabel.get_rect(center=browse.center))
        self._buttons["browse_image"] = browse
        y += 44

        if self.preview is not None:
            max_w, max_h = 240, 96
            iw, ih = self.preview.get_width(), self.preview.get_height()
            f = min(max_w / iw, max_h / ih, 1.0)
            thumb = pygame.transform.smoothscale(
                self.preview, (max(1, round(iw * f)), max(1, round(ih * f)))
            )
            rect = thumb.get_rect(midtop=(box.centerx, y))
            pygame.draw.rect(surface, (20, 22, 25), rect.inflate(8, 8), border_radius=6)
            surface.blit(thumb, rect)
            y += thumb.get_height() + 10
        return y
