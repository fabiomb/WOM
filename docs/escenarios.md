# Escenarios, editor de mapas y carga de mapa

Tres funciones que comparten un mismo artefacto portable: el contenedor
`.wom`. Diseñadas juntas (v0.5.0) para que crear, compartir y jugar mapas sea
coherente.

## El contenedor `.wom`

Un `.wom` es un **ZIP** con:

- `scenario.json` — el documento (ver esquema abajo).
- `image.png` — ilustración opcional del escenario.

Es un formato de *setup inicial*, **no un savegame**: guarda el mapa, los
sitios, las tropas sembradas, los jugadores y la metadata, pero **no** el
estado vivo (RNG, turno, comida). Al cargarlo se arranca una partida nueva en
turno 0 (`Game.from_setup`), con una seed de RNG aleatoria como cualquier
partida nueva.

Mismo formato para dos usos, distinguidos por **tener título o no**:

- **Escenario** — `.wom` con título. Se muestra su intro (título, descripción,
  imagen) y se juega con la **IA y la victoria que trae adentro**.
- **Mapa** — `.wom` sin título. Terreno + tropas para jugar como **partida
  nueva**, eligiendo IA y victoria en el menú ("Cargar mapa").

### Esquema de `scenario.json`

```json
{
  "format_version": 1,
  "saved_at": "2026-06-17T12:00:00",
  "meta": {
    "title": "El asedio del norte",
    "description": "Defendé el fuerte del norte…",
    "victory_mode": "flags",
    "ai_level": "dificil",
    "author": "",
    "has_image": true
  },
  "setup": {
    "world": { "...": "WorldMap.to_dict()" },
    "armies": [
      {"owner": 0, "position": [1, 1], "composition": {"soldado": 10}}
    ],
    "players": [
      {"id": 0, "name": "Jugador", "is_ai": false, "ai_level": null, "...": "…"},
      {"id": 1, "name": "Rival", "is_ai": true, "ai_level": "dificil", "...": "…"}
    ]
  }
}
```

`setup.world` es exactamente `WorldMap.to_dict()` (terreno por filas, fuertes y
pueblos con dueño). `setup.armies` es la lista de tropas sembradas (`owner`,
`position`, `composition`; `xp`/`food` opcionales).

## Módulo de persistencia (`wom/persistence/scenario.py`)

Stdlib puro (`zipfile` + `json`): **no importa pygame** (verificado en el smoke
test). La imagen viaja como bytes crudos; la UI los convierte a Surface.

- `ScenarioDoc` — dataclass con `world`, `players`, `army_specs`, `title`,
  `description`, `victory_mode`, `ai_level`, `author`, `image_bytes`.
  `is_scenario` ⇔ tiene título.
- `save_scenario(doc, name=None, directory=None) -> Path` — empaqueta el ZIP.
- `load_scenario(path) -> ScenarioDoc` — documento completo (con imagen).
- `scenario_info(path) -> dict` — resumen liviano para el menú (lee solo el
  JSON, sin extraer la imagen).
- `list_maps(directories=None) -> list[Path]` — `.wom` de las carpetas (default:
  distribuidos + usuario), más reciente primero.
- `build_game(doc, *, players=None, victory_mode=None) -> Game` — llama a
  `Game.from_setup`. Sin overrides = escenario completo (usa los jugadores y la
  victoria del `.wom`); con overrides = "Cargar mapa" (terreno+tropas del `.wom`
  pero jugadores/victoria del menú).

### Carpetas

- `data/scenarios/` (`BUNDLED_SCENARIOS_DIR`) — escenarios distribuidos con el
  juego (solo lectura; resuelve dentro del bundle de PyInstaller).
- `maps/` (`MAPS_DIR`) — lo que guarda el editor y comparte el usuario, junto al
  ejecutable (escribible, gitignored).

## Editor de mapas (`wom/ui/editor_screen.py`)

Pantalla con la misma forma que `GameScreen`. Mantiene un `Game` interno como
buffer de edición (mundo de pura pradera) y **reutiliza `MapRenderer`** para
dibujar el lienzo con el mismo aspecto que la partida. Tras pintar terreno llama
`MapRenderer.refresh_terrain()` para re-autotilar el agua en vivo.

Paleta de la sidebar:

- **Terreno** — los 6 `Terrain` (pradera, bosque, montaña, agua, puente-H,
  puente-V).
- **Sitios y tropas** — fuerte, pueblo, ejército, borrador.
- **Selector de dueño** — Neutral / Jugador 1 / Jugador 2 (las tropas solo J1/J2;
  el dueño Neutral cae a J1).
- **Menú** — Nuevo (tamaño preset → lienzo en blanco), Guardar, Guardar como…,
  Cargar (`ListModal` sobre `list_maps`), Generar aleatorio (confirma;
  `generate_map`), Volver al menú.

### Guardar / Guardar como (comportamiento de editor estándar)

El editor recuerda el archivo en edición (`current_path`) y su metadata
(`doc_meta` + `doc_image`):

- **Guardar** — si ya hay un archivo asociado, lo **sobreescribe** sin volver a
  pedir datos; si todavía no se guardó nunca, abre el formulario (como "Guardar
  como").
- **Guardar como…** — siempre abre el formulario (prellenado con la metadata
  actual) y crea un archivo nuevo, cuyo nombre deriva del título.
- **Cargar** un `.wom` pasa a editar ese archivo (el próximo "Guardar" lo pisa).
- **Nuevo** descarta el archivo asociado (documento sin guardar).

El sidebar muestra `Editando: <nombre>` (o `(sin guardar)`) para saber qué mapa
se está editando, y un aviso `Guardado: <archivo>.wom` al confirmar.

Clic izquierdo pinta/coloca (arrastre continuo para terreno y borrador); clic de
tropas sobre un ejército existente abre `TroopPicker` para editar su
composición. Rueda hace zoom y el botón del medio arrastra la vista (igual que
en la partida).

## Flujos del menú (`wom/ui/menu_screen.py` → `app.py`)

- **Escenarios** — lista `.wom` con título; al elegir uno, intro con
  texto+imagen y botón Jugar → `ScenarioChoice` → `scenario_game(choice)`.
- **Editor de mapas** — acción `"editor"` → `EditorScreen`.
- **Nueva partida → Origen del mapa: archivo** — modo `pick_map` lista todos los
  `.wom`; el elegido viaja en `NewGameChoice.map_path` y `new_game` lo usa como
  terreno+tropas con los jugadores/victoria del menú.

## Tests

- `tests/test_scenario.py` — roundtrip del `.wom`, `scenario_info`,
  `build_game`/`from_setup`, overrides, `list_maps`.
- `tests/test_editor.py` — operaciones de edición headless, guardar+recargar,
  `refresh_terrain`.
- `tests/test_ui_smoke.py` — `EditorScreen.draw`, flujo de Escenarios/intro y de
  origen-de-mapa en el menú.
