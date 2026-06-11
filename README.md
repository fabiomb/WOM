# WOM

Juego de estrategia militar 2D por turnos: humano contra la IA, sobre un mapa
generado aleatoriamente con fuertes que producen tropas, pueblos que dan
comida y batallas autoresueltas. Hecho en **Python 3.13 + pygame-ce**.

![Partida en curso](docs/screenshot_m2.png)

## El juego

- **Mapa aleatorio coherente**: cadenas montañosas, bosques en manchas, lagos
  y ríos con vados. Misma seed = mismo mapa (y misma partida: todo el azar es
  determinista).
- **Cuatro clases de unidad** (partisano, soldado, caballero, arquero) con
  bonus de terreno y piedra-papel-tijera entre clases. Los arqueros ignoran el
  bonus defensivo al atacar un fuerte.
- **Economía**: los pueblos producen comida, los fuertes la convierten en
  tropas que se acumulan en reserva; las tropas salen al mapa solo cuando el
  jugador crea o reabastece un ejército.
- **Batallas autoresueltas** al intentar entrar al tile de un enemigo: poder
  por composición, terreno, comida y experiencia; el perdedor se retira.
- **Tres niveles de IA** (fácil / medio / difícil) sobre un mismo motor de
  scoring de objetivos, balanceados por simulación masiva.
- **Tres modos de victoria**: conquista total, captura de banderas o límite de
  turnos.
- **Movimiento animado**, menú principal y partidas guardadas en JSON (una
  partida cargada continúa exactamente igual, RNG incluido).

| Menú | Nueva partida |
| --- | --- |
| ![Menú principal](docs/screenshot_menu_main.png) | ![Nueva partida](docs/screenshot_menu_new.png) |

## Cómo jugar

**Linux / macOS**
```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt   # pygame-ce, pytest
.venv/bin/python main.py
```

**Windows**
```powershell
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python main.py
```

Click en un ejército propio para seleccionarlo y click(s) en el mapa para
trazar su camino; click en un fuerte propio para crear ejércitos desde la
reserva; `Shift+click` en otro ejército propio aledaño para fusionarlos en
uno. `Enter` termina el turno, `G` guarda la partida, `M` abre el reproductor
de música, `ESC` vuelve al menú.

La banda de sonido suena desde `data/music` (un tema al azar al arrancar);
en **Opciones** se configura on/off, volumen, carpeta y orden
aleatorio/secuencial — todo queda guardado en `settings.json`.

### Modo headless (sin ventana)

```bash
# Linux
.venv/bin/python main.py --headless --seed 42      # IA vs IA por consola
.venv/bin/python main.py --headless --debug-ai     # con log de decisiones de la IA

# Windows
.venv\Scripts\python main.py --headless --seed 42
.venv\Scripts\python main.py --headless --debug-ai
```

## Documentación

- [`idea.md`](idea.md) — la idea original del juego.
- [`docs/especificaciones.md`](docs/especificaciones.md) — especificación
  técnica completa: modelo de dominio, fases del turno, fórmula de batalla,
  IA, persistencia y roadmap.
- [`CLAUDE.md`](CLAUDE.md) — guía de arquitectura para desarrollo (capas,
  invariantes, comandos).

## Arquitectura

Separación estricta en capas — `wom/core/` no importa pygame (hay un test que
lo verifica):

```
wom/core/         lógica pura: mapa, ejércitos, turnos, batallas, victoria
wom/ai/           jugadores IA (emiten las mismas Orders que un humano)
wom/ui/           pygame: render, input, menú, animaciones
wom/persistence/  savegames JSON en saves/
data/config/      todo el balance en JSON (clases, batalla, IA)
data/assets/      sprites PNG
```

## Assets

Los sprites viven en [`data/assets/`](data/assets/): tiles de terreno de
64×64 px, unidades de 48×48 px e íconos de 32×32 px (las banderas son
`flag_red`/`flag_blue` según el jugador y `flag` gris para sitios neutrales).
El arte se reemplaza por archivos del mismo nombre y tamaño; los placeholders
originales se conservan con prefijo `_` y se regeneran con
`tools/gen_placeholders.py`.

## Tests y herramientas

**Linux**
```bash
.venv/bin/python -m pytest tests -v                       # suite completa
PYTHONPATH=. .venv/bin/python tools/simulate.py --games 30  # balance IA vs IA
PYTHONPATH=. .venv/bin/python tools/screenshot_m2.py        # captura headless del juego
```

**Windows**
```powershell
.venv\Scripts\python -m pytest tests -v
$env:PYTHONPATH='D:\dev\WOM'; .venv\Scripts\python tools\simulate.py --games 30
$env:PYTHONPATH='D:\dev\WOM'; .venv\Scripts\python tools\screenshot_m2.py
```

Los scripts de `tools/` necesitan la raíz del proyecto en `PYTHONPATH`.

## Build distribuible

```bash
pip install pyinstaller
pyinstaller wom.spec    # genera dist/wom/ (ejecutable + data/ adentro)
```

El bundle es portable: `saves/` y `settings.json` se crean junto al
ejecutable. PyInstaller no cruza plataformas: el build de Linux se hace en
Linux — el workflow de GitHub Actions
([`build.yml`](.github/workflows/build.yml)) corre los tests y construye los
artefactos de Windows y Linux al taguear `v*` o ejecutándolo a mano.

## Autor

**Fabio Baccaglioni** — <fabiomb@gmail.com>
