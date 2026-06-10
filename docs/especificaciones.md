# WOM — Especificaciones Técnicas (v1)

Documento de arquitectura y especificación técnica derivado de `idea.md`.
Estado: borrador inicial para la etapa de planificación.

## 1. Stack tecnológico

| Componente | Elección | Justificación |
|---|---|---|
| Lenguaje | Python 3.13 | Ya instalado, ideal para lógica de juego y AI |
| Gráfica/Input | pygame-ce | 2D simple, multiplataforma, comunidad activa |
| Configuración | JSON | Clases, parámetros de AI y de juego editables sin tocar código |
| Persistencia | JSON (savegames) | Guardado/carga legible y debuggeable |
| Distribución | PyInstaller | Ejecutable único para Windows y Linux |
| Tests | pytest | El core es lógica pura, fácil de testear sin gráfica |

### Fiabilidad en el entorno de desarrollo

- Windows 11 + Python 3.13.7: soportado por pygame-ce con wheels precompilados (no requiere compilador).
- Linux: pygame-ce y PyInstaller funcionan igual; el build de Linux debe hacerse **en** Linux (PyInstaller no cross-compila). Opciones: WSL2, máquina virtual o CI (GitHub Actions).
- Rendimiento: juego por turnos 2D, sin riesgo. Python sobra para mapas de cientos de tiles y decenas de ejércitos.

## 2. Principio arquitectónico central

**El core del juego no importa pygame.** Separación estricta en tres capas:

```
┌─────────────────────────────────────────┐
│  ui/        pygame: render, input, menú │  ← depende de core
├─────────────────────────────────────────┤
│  ai/        jugador AI (emite Orders)   │  ← depende de core
├─────────────────────────────────────────┤
│  core/      lógica pura: estado, turnos,│  ← sin dependencias
│             mapa, batallas, victoria    │     externas
└─────────────────────────────────────────┘
```

Beneficios concretos para WOM:
- La AI se desarrolla y prueba **headless** (sin abrir ventana), miles de partidas simuladas por minuto para ajustar parámetros.
- Tests de batallas, producción y victoria sin gráfica.
- El multiplayer futuro (v2+) se monta sobre el mismo core: una partida es estado + lista de órdenes por turno.

## 3. Modelo de dominio (core/)

### 3.1 Mapa (`core/worldmap.py`, `core/mapgen.py`)

- Grilla de tiles `(ancho, alto)`. Cada tile tiene un `Terrain` (enum): `PLAINS`, `FOREST`, `MOUNTAIN`, `WATER` (intransitable).
- El terreno afecta: costo de movimiento y modificador de batalla.
- `Fort` y `Town` ocupan un tile, tienen dueño (`player_id` o neutral) y bandera.
- Los forts tienen además una **reserva** de tropas (`reserve`, por clase): la
  producción se acumula ahí y las tropas solo salen al mapa por una acción
  voluntaria del jugador (crear ejército o reabastecer uno estacionado).
  Un fuerte capturado pierde toda su reserva.
- **Generador**: recibe `MapParams(width, height, n_forts, n_towns, seed)`. Garantiza: forts iniciales de cada jugador en extremos opuestos, todo fort/town alcanzable (conectividad por flood-fill).
- El terreno se genera por **features coherentes** (no ruido tile a tile): cadenas montañosas (caminatas con dirección dominante, serpenteo y engrosamiento), bosques en manchas (crecimiento desde semillas), lagos y un río serpenteante que cruza el mapa dejando **vados** transitables cada 3-6 tiles para no cortar la conectividad. Las proporciones objetivo (`TERRAIN_TARGETS`: 20% bosque, 15% montaña, 10% agua) viven en `core/mapgen.py`.
- El mapa se serializa a JSON → "fácil de editar" y base del editor futuro.

### 3.2 Ejércitos (`core/army.py`)

```
Army:
  id, owner (player_id)
  position (tile)
  composition: {clase: cantidad}     # suma ≤ MAX_ARMY_SIZE (config, default 100)
  xp: int                            # 0 ⇒ ejército eliminado (cruz en el mapa)
  food: 0..100                       # eficiencia en combate
  path: [tiles]                      # órdenes pendientes (camino a seguir)
```

- Velocidad del ejército = `min(velocidad de clase presente)`.
- Recuperación de XP por turno: +10 en fort, +5 en town, +1 en otro tile (valores en config).

### 3.3 Clases de unidad (`data/config/classes.json`)

Las cuatro clases (`partisano`, `soldado`, `caballero`, `arquero`) se definen **solo** en config:

```json
{
  "caballero": {
    "nombre": "Caballero",
    "velocidad": 3,
    "ataque": 8,
    "defensa": 6,
    "bonus_terreno": {"plains": 1.3, "forest": 0.7},
    "bonus_vs": {"arquero": 1.5}
  }
}
```

Esquema por clase: `velocidad`, `ataque`, `defensa`, `bonus_terreno` (multiplicadores), `bonus_vs` (piedra-papel-tijera entre clases), `ignora_bonus_fort` (opcional: al atacar un fuerte la clase no sufre el bonus defensivo del fuerte; lo tiene el arquero). Agregar/balancear clases no toca código.

### 3.4 Turnos (`core/game.py`, `core/orders.py`)

Fases de un turno (orden fijo, determinista dado un seed):

1. **Órdenes** — cada jugador (humano vía UI, AI vía `ai/`) asigna `path` a sus ejércitos y/o emite `CreateArmyOrder` para crear un ejército en un fuerte propio con tropas de su reserva (hasta `max_army_size`).
2. **Movimiento** — todos los ejércitos avanzan según velocidad y costo de terreno. Si un ejército intenta entrar al tile de un enemigo ⇒ ambos se detienen y se encola una batalla.
3. **Batallas** — se resuelven todas las encoladas (ver 3.5).
4. **Captura** — fort/town con un ejército enemigo encima cambia de dueño; la reserva de un fuerte capturado se destruye.
5. **Producción** — towns: +1 comida al dueño; forts: producen tropas según fórmula `tropas_nuevas = floor(comida_disponible * tasa_produccion)` (tasa en config), consumiendo la comida usada. Las tropas se **acumulan en la reserva del fuerte** (cap `max_reserva_fort`): no salen al mapa sin una orden del jugador. El orden entre los fuertes de un jugador **rota por turno**: cuando la comida no alcanza para todos, cada fuerte (incluidos los capturados) produce a su turno en vez de que el primero acapare el stock.
6. **Recuperación** — XP según ubicación; un ejército estacionado en fuerte propio se **reabastece** desde la reserva hasta `max_army_size` (transferencia round-robin por clase) y come del stock del jugador; estacionado en un **pueblo propio come directamente del pueblo** (sin consumir stock). Ejércitos con XP ≤ 0 se eliminan (se registra cruz) y sus tropas restantes cuentan como bajas del jugador (`Player.troops_lost`, mostrado en el HUD).
7. **Victoria** — se evalúan las condiciones (ver 3.6). Si hay ganador, fin.

### 3.5 Batalla v1 (`core/battle.py`) — autoresuelta, sin zoom

```
poder(ejército) = Σ por clase [ cantidad × stat × bonus_vs × bonus_terreno ]
                  × factor_comida(food)      # 0.5 a 1.0 lineal
                  × factor_xp(xp)            # moral/experiencia
                  × random.uniform(1-σ, 1+σ) # σ en config (default 0.2)

donde stat = `ataque` para el atacante y `defensa` para el defensor.
La batalla ocurre cuando un ejército intenta entrar al tile de un enemigo:
cada bando pelea desde su propio tile (usa su propio bonus de terreno).
```

- Ratio de poderes determina resultado: victoria clara, empate o retirada (umbrales en config).
- Pérdidas: proporcionales al ratio; el perdedor pierde más; **la retirada penaliza extra** en XP y tropas.
- Defensa en fort/town otorga multiplicador defensivo (config: `bonus_defensa_fort` 1.5, `bonus_defensa_town` 1.2). Excepción: las clases atacantes con `ignora_bonus_fort` (los arqueros, que disparan por encima de las murallas) cancelan el bonus de fuerte para su propia contribución y pelean 1:1 contra el defensor.
- Toda la aleatoriedad usa el RNG de la partida (seed) ⇒ replays y tests deterministas.

### 3.6 Victoria (`core/victory.py`)

Evaluadas en este orden al final de cada turno:
1. **Victoria total**: el rival no tiene ejércitos ni forts.
2. **Captura de banderas**: un jugador controla todas las banderas objetivo (forts/towns marcados).
3. **Superioridad por tiempo**: al llegar al turno límite, gana quien tenga más territorio (tiles controlados) y, en empate, más tropas.

El modo de victoria activo se elige al crear la partida.

## 4. AI (`ai/`)

- Interfaz: `AIPlayer.decide_orders(game_state) -> list[Order]`. Misma API que el jugador humano ⇒ intercambiables, y AI vs AI para testing.
- **Motor de scoring de objetivos** (un solo código para los tres niveles): para cada ejército se puntúan los objetivos posibles — capturar fort/town, atacar (solo con ventaja ≥ `umbral_ataque`), defender fuerte amenazado, reabastecerse (tropas o comida) — con `score = valor / (1 + distancia / horizonte)`, y se elige el mejor.
- **Tres niveles** (`data/config/ai.json`): cada nivel es un set de pesos/parámetros, no código distinto:
  - `facil`: horizonte 1 (solo ve lo inmediato), ignora la economía, ataca casi siempre (umbral 0.8).
  - `medio`: evalúa distancias, defiende fuertes, ataca con ventaja numérica (umbral 1.1).
  - `dificil`: además activa tres capacidades exclusivas: `agrupa` (fuego concentrado: ataca si la fuerza combinada de los ejércitos cercanos supera el umbral), `coordina` (reparte objetivos de captura entre ejércitos en vez de amontonarlos) y `evita_peligro` (el ruteo rodea las zonas de enemigos más fuertes en vez de chocar de frente en batallas parejas decididas por el azar).
- Parámetros ajustables documentados: `agresividad`, `peso_economia`, `peso_defensa`, `horizonte`, `umbral_ataque`, `umbral_crear_ejercito`, `agrupa`, `coordina`, `evita_peligro`.
- La memoria de amenazas (posición previa de los enemigos respecto de los fuertes propios) sube la urgencia de defensa cuando un enemigo se acerca: la AI reacciona al movimiento del jugador.
- Cada decisión de la AI puede loguearse con su justificación (`--debug-ai`) para cumplir el requisito de "perfectamente documentada para mejorar y modificar".
- **Balance validado por simulación masiva** (`tools/simulate.py`): partidas AI vs AI con lados alternados. Resultado actual: medio > facil (~63%), dificil > medio (~60%), dificil > facil (~67%).

## 5. UI (`ui/`)

- **Menú** (v1, implementado en M4): Nueva partida (nivel de AI, tamaño de mapa chico/medio/grande y condición de victoria, opciones cíclicas por click), Cargar partida (lista de saves con turno y fecha), Salir. Guardar disponible durante la partida (botón del HUD o tecla G); ESC vuelve al menú.
- **Vista de mapa**: tiles con sprites PNG, ejércitos como íconos con contador de tropas, banderas de color por jugador (`flag_red`/`flag_blue`/`flag` gris para neutrales), cruces donde murieron ejércitos.
- **Sidebar**: por jugador muestra ejércitos, tropas activas, **bajas acumuladas**, fuertes, pueblos y comida; debajo, la lista "Tus ejércitos" con número, tropas y posición de cada ejército del humano (el seleccionado se resalta).
- **Órdenes**: click en ejército → click(s) en el mapa para trazar el camino → el path se dibuja. Botón "Fin del turno".
- **Movimiento animado**: al finalizar el turno los ejércitos se deslizan por los tiles que recorrieron (el core lo registra en `Game.last_moves`, retirada incluida), todos en simultáneo a velocidad constante. Enter/Espacio/click saltea la animación; el resto del input se bloquea mientras tanto. Los ejércitos que mueren animan su recorrido y desaparecen al final (queda la cruz); el overlay de fin de partida espera a que termine la animación del último turno.
- **Assets placeholder**: PNG planos generados por script (`tools/gen_placeholders.py`): tiles de 64×64 px, unidades de 48×48 px, íconos de 32×32 px. El arte final reemplaza archivos con el mismo nombre y tamaño.

## 6. Persistencia (`persistence/`)

- Savegame = JSON con: versión de formato (`format_version`), fecha de guardado, seed, mapa completo, estado de todos los ejércitos/forts/towns/jugadores (incluido el nivel de AI), número de turno, modo de victoria **y el estado interno del RNG**: una partida cargada continúa exactamente igual que la original (mismas batallas con la misma seed).
- La config de balance (`data/config/*.json`) no viaja en el savegame: se relee al cargar.
- Guardar en `saves/` con timestamp; cargar reconstruye `Game` exacto (validado por test de continuación determinista).

## 7. Estructura del proyecto

```
WOM/
├── wom/                  # paquete principal
│   ├── core/             # lógica pura (sin pygame)
│   ├── ai/               # jugadores AI
│   ├── ui/               # pygame: render, input, menú
│   └── persistence/      # save/load
├── data/
│   ├── config/           # classes.json, game.json, ai.json
│   └── assets/           # PNGs (placeholders primero)
├── saves/                # partidas guardadas
├── tools/                # gen_placeholders.py, etc.
├── tests/                # pytest sobre core/ y ai/
├── docs/                 # este documento
└── main.py               # punto de entrada
```

## 8. Roadmap propuesto

| Hito | Contenido | Validación |
|---|---|---|
| M1 | Core sin gráfica: mapa, generador, ejércitos, turnos, batalla, victoria | Tests + partida AI vs AI por consola |
| M2 | UI mínima: render del mapa, dar órdenes con mouse, fin de turno | Partida humano vs AI fácil jugable |
| M3 | AI media y difícil, balance de clases vía config | AI vs AI masivo, ajuste de parámetros |
| M4 | Menú completo, guardar/cargar, placeholders definitivos | Partida completa de punta a punta |
| M5 | Build distribuible Windows (PyInstaller) y Linux (CI o WSL2) | Ejecutable corre en máquina limpia |

Fuera de alcance v1 (explícito en idea.md): zoom de batalla táctica, editor de mapas, multiplayer.

## 9. Riesgos y mitigaciones

| Riesgo | Impacto | Mitigación |
|---|---|---|
| Build Linux desde Windows | Medio | WSL2 o GitHub Actions; decidir en M5 |
| Balance de clases | Medio | Todo en config + simulación AI vs AI masiva |
| Scope creep hacia v2 | Alto | Roadmap cerrado; zoom/editor/multiplayer explícitamente fuera de v1 |
| Rendimiento de render con mapas grandes | Bajo | Render por dirty-rects o por viewport; mapas v1 acotados |
