# WOM — Jugador LLM (`wom/llm/`)

Documento de diseño del módulo que permite que un **modelo de lenguaje (LLM)**
juegue WOM como un jugador más: observa el tablero, planifica, crea y mueve
ejércitos, reorganiza y pasa el turno. Estado: **implementado** (núcleo +
cliente de red), validado headless y contra un Ollama real.

Objetivo: poder enchufar un LLM —local (LM Studio, Ollama) u online (Gemini,
Claude, ChatGPT)— como rival, para **probar qué modelo juega mejor** un juego de
estrategia militar por turnos y cómo se comporta.

---

## 1. El problema, en términos de la arquitectura de WOM

WOM ya tiene resuelto lo difícil: un jugador es **"estado + una lista de `Order`
por turno"**. La AI lo demuestra:

```python
decide_orders(game: Game) -> list[Order]   # wom/ai/ai_player.py
```

Y lo que viaja por la red en el multiplayer es exactamente eso, serializado
(`wom/net/orders_codec.py`). Conectar un LLM **no es un problema de red** —la red
ya existe y es determinista (ver `docs/multiplayer.md`)—. El problema real es:

> Traducir `Game` → texto que el LLM entienda → su respuesta → `list[Order]`
> válidas.

Todo lo demás (handshake, lobby, lockstep, validación de órdenes por dueño, hash
de sincronía) lo aporta tal cual `ClientSession` + `NetGame`.

---

## 2. Metodología elegida

### Decisión: **programa intermediario que es cliente de red headless**, no MCP

El jugador-LLM es un proceso Python que se conecta a una partida multijugador
como **cliente (jugador 1)** y, en cada turno, en vez del motor de scoring de la
AI, le pregunta a un LLM y traduce la respuesta a órdenes. El núcleo es un
`LLMPlayer` con **la misma firma que `AIPlayer`**.

### Por qué intermediario y no un servidor MCP

| Criterio | Cliente intermediario (elegido) | Servidor MCP |
|---|---|---|
| Quién conduce el bucle | El cliente: "te toca → pensá → enviá órdenes → esperá al rival" | El host del LLM decide cuándo llamar (control invertido) |
| Encaje con el lockstep por turnos | Natural: un turno = una decisión | Forzado: el reloj de turno y la espera del rival no encajan |
| Protocolo | Reusa el de WOM (TCP + JSON) | Agrega MCP **encima** del protocolo propio, sin ganar nada |
| Multi-proveedor | Un `LLMBackend` con adapters | Igual de fácil, pero con la capa extra |

MCP serviría si quisiéramos exponer **WOM como herramienta** a un agente externo
que no controlamos (p. ej. que Claude Desktop "use" WOM). No es el caso: queremos
un jugador autónomo. Lo que **sí** se usa del mundo function-calling es el
**JSON estructurado** como formato de salida del modelo —pero dentro del proceso,
no como un servidor MCP.

### Decisión clave: el LLM emite **intenciones**, no órdenes crudas

Si al modelo se le piden `army_id` + path tile-por-tile, alucina coordenadas
inválidas constantemente. En cambio recibe un **vocabulario de intenciones de
alto nivel** ("mover el ejército 7 hacia (12,4)", "crear ejército en mi fuerte
(3,5)") y `actions.py` calcula la ruta con el pathfinding del core y valida
contra el estado. Es el patrón function-calling: se define el "schema" de
acciones y el modelo rellena los huecos.

---

## 3. Arquitectura

Se respeta la separación en capas. Nuevo paquete **`wom/llm/`**, que **no importa
pygame** (igual que `core`, `ai` y `net`; está en el test de humo). Depende solo
del core.

```
┌───────────────────────────────────────────────┐
│ ui/    (sin cambios)                           │ ← pygame
├───────────────────────────────────────────────┤
│ net/   transporte, protocolo, sesión, lockstep │ ← solo core
├───────────────────────────────────────────────┤
│ llm/   observación, acciones, prompt, backend  │ ← solo core (+ urllib)
├───────────────────────────────────────────────┤
│ ai/    (sin cambios; misma interfaz decide_orders)
├───────────────────────────────────────────────┤
│ core/  (sin cambios)                           │ ← sin dependencias
└───────────────────────────────────────────────┘
```

Módulos de `wom/llm/`:

- **`observation.py`** — `Game` → vista del jugador. `build_observation(game,
  player_id)` arma un dict estructurado y determinista (turno, comida, modo de
  victoria, mapa ASCII, listas de ejércitos y sitios). `render_text(...)` lo
  vuelca al texto que va en el prompt. Los ejércitos propios se muestran con
  composición exacta; los enemigos, solo con posición y total (lo razonablemente
  observable); las reservas, solo de los fuertes propios.
- **`actions.py`** — gramática de acciones ↔ `Order`. `translate_actions(actions,
  game, player_id) -> (orders, warnings)` traduce las intenciones a órdenes
  válidas y devuelve los motivos de lo descartado. Valida existencia, dueño,
  adyacencia, alcanzabilidad (Dijkstra del core) y cantidades. Tolera ids como
  `"#7"`/`"7"` y coordenadas como `[x,y]` o `{"x":..,"y":..}`.
- **`prompt.py`** — `system_prompt(game, player_id)`: arma el prompt de sistema
  **desde la config real** (clases y sus stats, modo de victoria, `max_army_size`)
  para que las reglas que ve el modelo coincidan con la config cargada.
- **`backend.py`** — `LLMBackend` (ABC) + adapters; solo `urllib` (sin SDKs).
- **`agent.py`** — `LLMPlayer.decide_orders(game) -> list[Order]`.

---

## 4. La observación (qué "ve" el LLM)

`render_text` produce un bloque compacto: cabecera (turno, comida, victoria),
un **mapa ASCII** con índices de fila/columna y marcadores, y listas
estructuradas. Marcadores del mapa (prioridad ejército > fuerte > pueblo):

```
Leyenda: . llano  f bosque  ^ montaña  ~ agua  = puente |
         A ejército propio  E enemigo |
         F/X/+ fuerte propio/enemigo/neutral | T/Y/- pueblo propio/enemigo/neutral
```

Más abajo, las listas dan la verdad detallada: mis ejércitos (id, posición,
composición por clase, total, xp, comida), ejércitos enemigos visibles (id,
posición, total aproximado), fuertes (dueño y reserva si es propia) y pueblos.
El mapa da la *gestalt* espacial; las listas, los datos exactos que el modelo
referencia por coordenada/id.

> **Tokens:** la observación está pensada para ser compacta. En mapas grandes el
> mapa ASCII crece con el área; si el costo se vuelve un problema, conviene
> recortar (radio de visión, resumen por regiones) antes que volcar todo.

---

## 5. La gramática de acciones

El modelo responde **únicamente** con un arreglo JSON de acciones:

| Acción | Forma | Se traduce a |
|---|---|---|
| Mover | `{"action":"move","army":7,"to":[12,4]}` | `MoveOrder` (ruta por Dijkstra) |
| Crear | `{"action":"create","fort":[3,5]}` | `CreateArmyOrder` |
| Fusionar | `{"action":"merge","source":7,"target":8}` | `MergeArmyOrder` |
| Dividir | `{"action":"split","source":7,"detach":{"soldado":10}}` | `SplitArmyOrder` |
| Transferir | `{"action":"transfer","source":7,"target":8,"troops":{"arquero":5}}` | `TransferTroopsOrder` |
| Pasar | `{"action":"wait"}` | (nada) |

`translate_actions` es **defensivo**: el lockstep ya filtra por dueño
(`NetGame._validate_peer`), pero acá se valida además para no gastar órdenes y
para poder explicarle al modelo qué rechazó. Toda acción inválida se descarta con
un motivo legible (que el agente puede loguear o, en el reintento, devolverle al
modelo).

---

## 6. Backends (proveedores)

`LLMBackend.complete(system, user) -> str` es la única operación. Las
implementaciones hablan la API REST de cada proveedor con `urllib` (sin
dependencias nuevas):

- **`OpenAICompatible`** — endpoint estilo OpenAI `/chat/completions`. **Un solo
  adapter cubre Ollama** (`http://localhost:11434/v1`), **LM Studio**
  (`http://localhost:1234/v1`) y **OpenAI** (`https://api.openai.com/v1`).
- **`Gemini`** — API `generativelanguage` de Google.
- **`Anthropic`** — API `/v1/messages` de Claude.

`make_backend(BackendConfig)` arma el backend según `provider`, resolviendo la
URL base por defecto y la API key desde variable de entorno si no se pasa
(`OPENAI_API_KEY`, `GEMINI_API_KEY`/`GOOGLE_API_KEY`, `ANTHROPIC_API_KEY`). El
parseo de la respuesta de cada proveedor vive en funciones puras
(`parse_*_response`), testeables sin red.

**Razonamiento extendido (Anthropic).** Para exprimir el razonamiento de Opus
4.8 (o 4.7 / Fable 5), `BackendConfig.thinking=True` activa **adaptive thinking**
y `effort` (`low|medium|high|xhigh|max`) regula la profundidad — ambos GA, sin
beta header. `Anthropic.build_payload` además **omite `temperature`** en esos
modelos (la rechazan con 400) y da headroom de `max_tokens` para que el
pensamiento no trunque el JSON. Los bloques de "thinking" se ignoran al parsear;
solo se usa el texto final. Para Ollama/LM Studio/OpenAI esto no aplica.

---

## 7. El agente (`LLMPlayer`)

Por turno:

1. Arma `system_prompt(game, player_id)` (reglas) y `render_text(...)`
   (observación).
2. Llama al backend.
3. **Extrae el JSON de forma tolerante** (`extract_actions`): acepta un arreglo
   pelado, `{"actions":[...]}`, una sola acción, o cualquiera envuelto en un
   bloque ```` ```json ```` o en prosa.
4. Traduce con `translate_actions` y devuelve `list[Order]`.

Si el modelo falla (red, JSON irrecuperable), **reintenta una vez** con un mensaje
correctivo y, si vuelve a fallar, **pasa el turno** (lista vacía) en lugar de
romper la partida — el lockstep tolera órdenes vacías.

Como `LLMPlayer` clona la interfaz de `AIPlayer`, sirve en tres contextos sin
cambios: rival en red, reemplazo de la AI en single-player, y jugador en un
`Game` local para benchmarks.

---

## 8. El cliente de red (`tools/llm_client.py`)

Reutiliza toda la infraestructura de `wom/net/`. Flujo:

```
connect(host, port)            → Connection (TCP)
ClientSession(conn, name)      → manda Hello (handshake)
run_lobby():
  Connected   → conectado al host
  GameReady   → guarda el setup y marca "Listo"
  Started     → Game.from_dict(setup.state)   (mismo estado que el host)
play():
  NetGame(session, game, human_id=1, is_host=False)
  por turno (fase COLLECTING):
    orders = LLMPlayer.decide_orders(game)
    net.submit_local_orders(orders)   → viajan como ORDERS(turn)
    net.update()                       → al llegar las del rival, run_turn local
```

El host (un humano) crea la partida desde **Multijugador → Crear** y espera
conexiones; el cliente LLM se conecta por LAN/IP. El determinismo **no** depende
del LLM: este solo *produce* las órdenes; una vez emitidas, ambos lados corren el
mismo `run_turn`. El no-determinismo del modelo vive **fuera** de la simulación.

Ejemplos:

```powershell
# Gemma3 local por Ollama (sin API key)
.venv\Scripts\python.exe tools\llm_client.py --provider ollama --model gemma3 --name Gemma

# Otra máquina de la LAN
.venv\Scripts\python.exe tools\llm_client.py --host 192.168.1.20 --provider ollama --model gemma3

# Online (la key se toma del entorno)
.venv\Scripts\python.exe tools\llm_client.py --provider gemini    --model gemini-2.5-flash --name Gemini
.venv\Scripts\python.exe tools\llm_client.py --provider anthropic --model claude-opus-4-8  --name Claude

# Opus 4.8 a fondo: razonamiento extendido + esfuerzo alto
$env:ANTHROPIC_API_KEY = "sk-ant-..."
.venv\Scripts\python.exe tools\llm_client.py --provider anthropic --model claude-opus-4-8 \
    --name Claude --thinking --effort high
```

> **Reloj de turno:** una llamada a API tarda segundos. Conviene crear la partida
> con tiempo por turno **infinito** (0); si no, al vencer el reloj el host
> auto-envía y el LLM puede llegar tarde con sus órdenes.

---

## 9. Tests

Todo headless, sin LLM real ni HTTP salvo donde se indique:

- `tests/test_llm.py` — observación (campos, ocultar reservas/composición
  enemiga), traducción de cada acción (válidas e inválidas), extracción tolerante
  de JSON, parseo de las tres respuestas de backend, `make_backend` (defaults y
  key por entorno) y el agente con un `FakeBackend` (incluye el reintento).
- `tests/test_llm_net.py` — **integración por loopback**: host real + `LLMPlayer`
  con un backend guionado; reconstruye el juego con `Game.from_dict(setup.state)`
  y juega varios turnos manteniendo la sincronía determinista
  (`host.to_dict() == client.to_dict()` cada turno, sin desync).
- `tests/test_smoke.py` — extendido: `wom.llm` tampoco importa pygame.

---

## 10. Riesgos y mitigaciones

| Riesgo | Mitigación |
|---|---|
| El modelo devuelve JSON malo / con prosa | `extract_actions` tolerante + reintento + pasar turno |
| Alucina ids/coords/clases | `translate_actions` valida todo y descarta con motivo; el lockstep filtra por dueño |
| Modelo chico no razona la estrategia | Es esperable (lo que el benchmark mide); usar modelos más grandes |
| Latencia de la API vs reloj de turno | Recomendar tiempo por turno infinito para partidas con LLM |
| Costo de tokens | Observación compacta; recortar visión en mapas grandes si hace falta |
| La red bloquea durante la llamada al LLM | El hilo lector de `Connection` sigue buffereando; se drena al volver |

---

## 11. Estado y próximos pasos

**Hecho:** `wom/llm/` (observación, acciones, prompt, backends Ollama/LM
Studio/OpenAI/Gemini/Anthropic, agente) + `tools/llm_client.py`; tests verdes;
probado contra un Ollama real (`gemma3:1b` — demasiado chico, rompe el schema; el
pipeline lo maneja con gracia y pasa turno).

**Pendiente / ideas:**

- **`tools/llm_bench.py`** — benchmark headless **sin red**: un `Game` local con
  `LLMPlayer` vs `AIPlayer` (o LLM vs LLM), estilo `tools/simulate.py`, para
  comparar modelos de forma barata y determinista. Es el camino más corto a
  "qué LLM es mejor".
- Probar modelos más grandes (gemma3:12b, Gemini, Claude) y registrar resultados.
- Memoria/plan entre turnos (el LLM hoy decide turno a turno, sin estado propio).
- Opcional: function-calling/structured-output nativo de cada API en vez de
  parseo tolerante, donde el proveedor lo soporte.
