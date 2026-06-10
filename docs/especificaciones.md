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
- **Generador**: recibe `MapParams(width, height, n_forts, n_towns, seed)`. Garantiza: forts iniciales de cada jugador en extremos opuestos, todo fort/town alcanzable (conectividad por flood-fill), distribución pseudo-uniforme.
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

Esquema por clase: `velocidad`, `ataque`, `defensa`, `bonus_terreno` (multiplicadores), `bonus_vs` (piedra-papel-tijera entre clases). Agregar/balancear clases no toca código.

### 3.4 Turnos (`core/game.py`, `core/orders.py`)

Fases de un turno (orden fijo, determinista dado un seed):

1. **Órdenes** — cada jugador (humano vía UI, AI vía `ai/`) asigna `path` a sus ejércitos y/o emite `CreateArmyOrder` para crear un ejército en un fuerte propio con tropas de su reserva (hasta `max_army_size`).
2. **Movimiento** — todos los ejércitos avanzan según velocidad y costo de terreno. Si un ejército intenta entrar al tile de un enemigo ⇒ ambos se detienen y se encola una batalla.
3. **Batallas** — se resuelven todas las encoladas (ver 3.5).
4. **Captura** — fort/town con un ejército enemigo encima cambia de dueño; la reserva de un fuerte capturado se destruye.
5. **Producción** — towns: +1 comida al dueño; forts: producen tropas según fórmula `tropas_nuevas = floor(comida_disponible * tasa_produccion)` (tasa en config), consumiendo la comida usada. Las tropas se **acumulan en la reserva del fuerte** (cap `max_reserva_fort`): no salen al mapa sin una orden del jugador.
6. **Recuperación** — XP según ubicación; un ejército estacionado en fuerte propio se **reabastece** desde la reserva hasta `max_army_size` (transferencia round-robin por clase); ejércitos con XP ≤ 0 se eliminan (se registra cruz).
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
- Defensa en fort/town otorga multiplicador defensivo (config).
- Toda la aleatoriedad usa el RNG de la partida (seed) ⇒ replays y tests deterministas.

### 3.6 Victoria (`core/victory.py`)

Evaluadas en este orden al final de cada turno:
1. **Victoria total**: el rival no tiene ejércitos ni forts.
2. **Captura de banderas**: un jugador controla todas las banderas objetivo (forts/towns marcados).
3. **Superioridad por tiempo**: al llegar al turno límite, gana quien tenga más territorio (tiles controlados) y, en empate, más tropas.

El modo de victoria activo se elige al crear la partida.

## 4. AI (`ai/`)

- Interfaz: `AIPlayer.decide_orders(game_state) -> list[Order]`. Misma API que el jugador humano ⇒ intercambiables, y AI vs AI para testing.
- **Tres niveles** (`data/config/ai.json`): cada nivel es un set de pesos/parámetros, no código distinto:
  - `facil`: horizonte 1 turno, ignora comida, agresividad fija.
  - `medio`: evalúa distancias (Dijkstra sobre costos de terreno), defiende forts, ataca con ventaja numérica.
  - `dificil`: además gestiona economía (comida/producción), agrupa ejércitos, reacciona a movimientos del jugador (memoria de posiciones vistas).
- Parámetros ajustables documentados: `agresividad`, `peso_economia`, `peso_defensa`, `horizonte`, `umbral_ataque` (ratio de fuerza mínimo para atacar).
- Cada decisión de la AI puede loguearse con su justificación (`--debug-ai`) para cumplir el requisito de "perfectamente documentada para mejorar y modificar".

## 5. UI (`ui/`)

- **Menú** (v1): Nueva partida (parámetros de mapa + nivel AI + condición de victoria), Cargar partida, Salir. Guardar disponible durante la partida.
- **Vista de mapa**: tiles con sprites PNG, ejércitos como íconos con contador de tropas, banderas de color por jugador, cruces donde murieron ejércitos.
- **Órdenes**: click en ejército → click(s) en el mapa para trazar el camino → el path se dibuja. Botón "Fin del turno".
- **Assets placeholder**: PNG planos generados por script (`tools/gen_placeholders.py`): tiles de 64×64 px, unidades de 48×48 px, íconos de 32×32 px. El arte final reemplaza archivos con el mismo nombre y tamaño.

## 6. Persistencia (`persistence/`)

- Savegame = JSON con: versión de formato, seed, parámetros de partida, mapa completo, estado de todos los ejércitos/forts/towns, número de turno, modo de victoria.
- Guardar en `saves/` con timestamp; cargar reconstruye `Game` exacto.

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
