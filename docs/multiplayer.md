# WOM — Diseño de Multiplayer (v0.4.0)

Documento de diseño del modo multijugador, derivado del punto 11 de
`especificaciones.md`. Estado: **propuesta de diseño para revisión** (todavía
sin implementar). Define la metodología, la arquitectura, el protocolo de red,
la integración con el core y un plan de implementación por fases.

Objetivo de la versión **0.4.0**: dos jugadores humanos enfrentados por red
(LAN o IP directa), uno hospeda (host) y otro se conecta (cliente). Sin AI en
la partida en red (v0.4.0); la AI se mantiene para el modo de un jugador.

---

## 1. Alcance v0.4.0

Incluye (del punto 11 de `especificaciones.md`):

- Partida humano vs humano por TCP/IP, 1 host + 1 cliente.
- El host define las reglas: condición de victoria, turnos máximos y tiempo por
  turno (infinito o N segundos).
- Nombre de jugador (host al crear, cliente al conectarse).
- Chat entre jugadores en el sidebar.
- Menú **Multijugador** → crear partida (config de reglas + esperar conexiones)
  o conectarse (IP + puerto).
- Pantalla de espera con estado de conexión; aviso al host cuando entra el
  cliente.
- Diálogo de "listo" para ambos; la partida arranca cuando los dos confirman.
- El host puede dar de baja la partida; un diálogo avisa que desconectará al
  jugador externo.

**Fuera de alcance v0.4.0** (posible v0.5+):

- Más de 2 jugadores, espectadores, equipos.
- Matchmaking / servidor central / NAT traversal (se asume LAN o port-forward).
- Reconexión tras caída (una desconexión termina la partida).
- Guardar/cargar una partida en red a mitad de juego.
- Cifrado/autenticación fuerte (se asume red de confianza).

---

## 2. Metodología elegida

### Decisión: **lockstep determinista con host autoritativo de respaldo**

Ambos clientes ejecutan **la misma simulación** (`Game.run_turn`) con **las
mismas órdenes** y **el mismo estado de RNG**. Por la red viaja únicamente lo
mínimo: la seed + reglas al inicio, y la **lista de órdenes de cada jugador**
por turno. El host es la autoridad de respaldo: cada turno se intercambia un
**hash del estado**; si los estados divergen, el host reenvía el estado
completo y el cliente se resincroniza (no debería ocurrir si la determinación
se mantiene; es una red de seguridad ante bugs).

```
Turno T en cada cliente:
  1. El humano local arma sus órdenes (paths, crear/fusionar/dividir).
  2. Al "Fin del turno" (o al vencer el reloj) las envía al par: ORDERS(T).
  3. Espera ORDERS(T) del par.
  4. Con AMBAS listas + la seed compartida ejecuta game.run_turn(órdenes).
  5. Calcula hash(game) y lo compara con el del par (HASH(T)).
     - coincide  → sigue al turno T+1.
     - difiere   → el host envía STATE_SYNC con game.to_dict(); el cliente
                   recarga y continúa.
```

### Por qué lockstep y no "host autoritativo puro" (state streaming)

El core de WOM ya es **determinista dada la seed** (invariante del proyecto:
batallas y mapa reproducibles; el savegame ya guarda el estado del RNG para
continuar idéntico). Sobre esa base:

| Criterio | Lockstep (elegido) | Host autoritativo puro |
|---|---|---|
| Ancho de banda | Mínimo: solo órdenes (~cientos de bytes/turno) | Alto: estado completo cada turno |
| Reuso del core | Total: `run_turn` ya existe y es puro | Igual, pero el cliente solo renderiza |
| Animaciones | Cada cliente las arma localmente desde `last_moves`/`last_clashes` (ya existen) | Habría que serializar y enviar esos transitorios |
| Riesgo | Divergencia de determinismo entre máquinas | Latencia/picos al enviar estado grande |
| Encaje con la arquitectura | El propio doc del proyecto dice "una partida es estado + lista de órdenes por turno" | Menos idiomático |

El **único riesgo real** del lockstep es la divergencia de determinismo entre
máquinas; la sección 3 lo acota y el hash + STATE_SYNC lo cubre.

### Alternativas descartadas

- **State streaming puro** (host simula, envía estado, cliente solo dibuja):
  desperdicia la pureza del core y obliga a serializar transitorios de
  animación; mayor ancho de banda. Útil solo si el determinismo fuera
  imposible — no es el caso.
- **Rollback netcode** (estilo fighting games): innecesario; WOM es por turnos,
  no hay presión de latencia sub-segundo.

---

## 3. Garantías de determinismo

Lo que **ya** juega a favor (no tocar sin cuidado):

- Toda la aleatoriedad pasa por `Game.rng` (un único `random.Random` con seed).
  Mersenne Twister es **idéntico entre plataformas** dada la misma seed y
  secuencia de llamadas.
- Aritmética de batalla en `float` IEEE-754: CPython no reordena operaciones;
  la misma secuencia da el mismo resultado en Windows/Linux/macOS.
- `dict` ordenado por inserción y `sorted()` estable: el orden de iteración es
  determinista (p. ej. `_move_armies` ordena por `army.id`, `_add_to_reserve`
  y `_resupply` iteran `sorted(self.classes)`).
- Los `army.id` se asignan por `next_army_id` de forma determinista; como ambos
  clientes corren la misma simulación, los ids quedan sincronizados y una orden
  del par referencia ids que existen idénticos de los dos lados.

Lo que hay que **cuidar** para no romper el lockstep:

1. **Mismas órdenes, mismo orden.** Al combinar las órdenes de ambos jugadores
   antes de `run_turn`, ordenarlas de forma canónica (p. ej. por `(owner,
   army_id, tipo)`) en ambos clientes. Hoy `run_turn` aplica reorganizaciones
   antes que los movimientos; eso se conserva.
2. **Reorganizaciones del humano vía órdenes, no inmediatas.** En un jugador,
   fusionar/dividir se aplica al instante (`Game.merge_armies`/`split_army`
   fuera de las fases). En red eso **rompe el lockstep** (el par no lo ve hasta
   resolver el turno). Solución: en modo red el humano emite
   `MergeArmyOrder`/`SplitArmyOrder` por el flujo normal de órdenes —
   exactamente como ya lo hace la AI— y se aplican en `_apply_orders`. La UI
   muestra la reorganización como "pendiente" hasta el fin de turno.
3. **Sets transitorios** (`last_retreats`) solo alimentan la UI; no influyen en
   el estado del juego, así que su orden de iteración no afecta el determinismo.
4. **Config de balance idéntica.** Ambos clientes deben tener los mismos
   `data/config/*.json`. El host envía en el lobby una **huella (hash) de su
   config**; si no coincide con la del cliente, se aborta con un mensaje claro
   (evita divergencias silenciosas por configs editadas).
5. **Misma versión de WOM y de formato de protocolo**: se valida en el handshake.

El **hash de estado por turno** (sección 6, `HASH`) es la verificación
empírica: si algo de lo anterior falla, se detecta el primer turno que diverge.

---

## 4. Arquitectura

Se respeta la separación en capas. Nuevo paquete **`wom/net/`**, que **no
importa pygame** (igual que el core y la AI; se agrega al test de humo).
Depende solo del core (serializa/deserializa `Order` y `Game`).

```
┌───────────────────────────────────────────────┐
│ ui/    pantallas multijugador, chat, lobby     │ ← pygame + net + core
├───────────────────────────────────────────────┤
│ net/   transporte TCP, protocolo, sesión       │ ← solo core, SIN pygame
├───────────────────────────────────────────────┤
│ ai/    (sin cambios; no participa en red)      │
├───────────────────────────────────────────────┤
│ core/  (sin cambios funcionales)               │ ← sin dependencias
└───────────────────────────────────────────────┘
```

Módulos previstos en `wom/net/`:

- `protocol.py` — catálogo de mensajes (dataclasses) + (de)serialización JSON y
  framing por longitud. **Puro y testeable** sin sockets.
- `transport.py` — envoltura de `socket` TCP: hilo lector que entrega mensajes
  a una cola, envío con framing. Maneja conexión, cierre y errores de red.
- `session.py` — máquina de estados de la sesión (lobby → ready → jugando →
  terminada/desconectada), validación de órdenes del par, hash de estado.
  Lado host y lado cliente comparten esta lógica con roles.
- `orders_codec.py` — `Order ↔ dict` (las dataclasses de `core/orders.py` no
  saben serializarse; el codec vive en net para no contaminar el core).

Serialización de `Game`: ya existe (`to_dict`/`from_dict`) y se reutiliza para
el `STATE_SYNC` y para enviar el estado inicial si se prefiere (ver 5.1).

---

## 5. Modelo de la partida en red

### 5.1 Roles e inicio

- **Host** = `player 0` (`human_id=0`). **Cliente** = `player 1`
  (`human_id=1`). `GameScreen` ya recibe `human_id`; se reutiliza tal cual.
- El host genera el `Game` (seed, mapa, reglas) y manda al cliente lo necesario
  para **regenerarlo idéntico**: `seed`, `MapParams` (tamaño/forts/towns), modo
  de victoria, reglas (turnos máx, tiempo por turno), nombres de ambos
  jugadores y la huella de config. El cliente hace `Game.new(...)` con esos
  parámetros → mismo mapa y mismos ejércitos iniciales, sin transmitir el mapa.
  - **Decisión (v0.4.0): el host envía directamente `game.to_dict()`** y el
    cliente hace `Game.from_dict()`. Más bytes (una sola vez) pero a prueba de
    cualquier diferencia de generación; el costo es despreciable (ocurre una vez,
    al empezar). Por eso `GAME_SETUP` transporta el estado completo en `state`.

### 5.2 Máquina de estados de la sesión

```
        crear                     conectar
   ┌──────────────┐          ┌──────────────────┐
   │   HOST_WAIT  │          │  CLIENT_CONNECT  │
   │ (escuchando) │          │  (IP + puerto)   │
   └──────┬───────┘          └────────┬─────────┘
          │  cliente conecta + handshake OK       │
          └──────────────┬────────────────────────┘
                         ▼
                     ┌────────┐  ambos "Listo"   ┌──────────┐
                     │ LOBBY  ├─────────────────►│ PLAYING  │
                     └───┬────┘                  └────┬─────┘
                         │ host cancela / error       │ victoria / desconexión
                         ▼                            ▼
                     ┌──────────────────────────────────┐
                     │            CLOSED                 │
                     └──────────────────────────────────┘
```

### 5.3 Flujo de un turno (lockstep)

Los dos jugadores juegan el **mismo turno en simultáneo** (como el humano y la
AI hoy): cada uno arma sus órdenes en privado, y al confirmar se revelan juntas.

1. Ambos clientes están en el turno `T`, recolectando órdenes locales.
2. Al "Fin del turno" (botón/tecla) o al vencer el reloj de turno, el cliente
   envía `ORDERS(T, lista)` y marca su lado como "listo".
3. Cuando un cliente tiene **sus** órdenes y las **del par**, ejecuta
   `run_turn(combinadas)`, dispara la animación local y pasa a `T+1`.
4. Intercambian `HASH(T+1)`. Mismatch → STATE_SYNC desde el host.

Mientras un jugador espera al otro, la UI muestra "Esperando al rival…". El
reloj de turno (si está activo) evita esperas infinitas: al llegar a 0, las
órdenes parciales (o vacías) de ese jugador se envían automáticamente.

### 5.4 Validación de órdenes del par

Antes de aplicar las órdenes recibidas, la sesión las **filtra por dueño**: una
orden del par solo puede afectar ejércitos/fuertes de `player 1` (o del que
corresponda). Órdenes inválidas se descartan (defensa ante cliente
buggeado/malicioso). El host es el árbitro: si detecta órdenes ilegales,
puede registrar y descartar.

---

## 6. Protocolo de red

- **Transporte**: TCP (stdlib `socket`), conexión directa host↔cliente. Sin
  dependencias externas (coherente con el stack: pygame-ce + stdlib).
- **Framing**: cada mensaje es `uint32 big-endian (longitud) + payload JSON
  UTF-8`. Simple, suficiente, fácil de testear.
- **Hilo de red**: un hilo lector por socket vuelca mensajes deserializados a
  una `queue.Queue`; el loop de pygame la **drena una vez por frame** (no
  bloquea el render). El envío se hace desde el hilo principal (o un lock).
- **Puerto por defecto**: configurable; sugerido `50000` (editable en la UI).

### Catálogo de mensajes (`type` + campos)

| Mensaje | Dirección | Contenido | Cuándo |
|---|---|---|---|
| `HELLO` | cliente→host | versión WOM, versión protocolo, huella config, nombre | al conectar |
| `WELCOME` | host→cliente | acepta/rechaza (+motivo), nombre del host | respuesta a HELLO |
| `GAME_SETUP` | host→cliente | seed, MapParams, victory_mode, reglas, nombres, (o `game.to_dict()`) | al pasar a LOBBY |
| `READY` | ambos | `bool listo` | en LOBBY |
| `START` | host→cliente | confirma arranque (turno 0) | ambos listos |
| `ORDERS` | ambos | `turn`, lista de órdenes serializadas | cada fin de turno |
| `HASH` | ambos | `turn`, hash del estado tras resolver | tras cada `run_turn` |
| `STATE_SYNC` | host→cliente | `game.to_dict()` autoritativo | ante mismatch de HASH |
| `CHAT` | ambos | `nombre`, `texto`, timestamp | en cualquier momento |
| `PING`/`PONG` | ambos | keepalive / latencia | periódico |
| `BYE` | ambos | motivo (host canceló, salida, error) | al cerrar |

Serialización de órdenes (`orders_codec.py`): cada `Order` → `{"kind": "...",
...campos}` y de vuelta. `MoveOrder.path` (tupla de tuplas) → lista de listas;
`SplitArmyOrder.composition` (tupla de pares) → objeto. Round-trip cubierto por
tests.

Hash de estado: `hash` estable y multiplataforma sobre una proyección
canónica del `Game` (p. ej. `sha1` de un JSON ordenado de
posiciones/composición/xp/food de ejércitos + dueños de sitios + comida + turno
+ estado del RNG). Determinista, barato.

---

## 7. Reglas configurables por el host

Se definen al crear la partida (pantalla de reglas) y viajan en `GAME_SETUP`:

- **Condición de victoria**: las que ya existen (`VictoryMode.TOTAL / FLAGS /
  TIME`).
- **Turnos máximos** (implementado MP5): el host lo fija en `Game.turn_limit`,
  que **se hornea en el estado inicial** (`to_dict`) y lo evalúa el core de
  forma idéntica en ambos clientes — al alcanzarlo, desempate por territorio y
  luego tropas (como `TIME`), en cualquier modo de victoria. Así el límite no
  depende de que ambos lados apliquen una regla externa por su cuenta.
- **Tiempo por turno** (implementado MP5): `infinito` (0) o `N` segundos. Es una
  regla de **UI** (`GameScreen`): el reloj corre mientras el humano puede dar
  órdenes (no durante la espera del rival ni la animación) y al llegar a 0
  auto-envía las órdenes que haya. No afecta el determinismo (solo decide
  *cuándo* se emiten las órdenes locales).

El resto (`turn_seconds`, `max_turns`) viaja en `MatchRules`
(`wom/net/rules.py`) dentro de `GAME_SETUP.rules`.

---

## 8. UI

Pantallas nuevas (en `wom/ui/`), siguiendo el estilo del menú actual
(pergamino/tinta):

- **Menú principal**: nueva opción **"Multijugador"** entre "Nueva partida" y
  "Opciones".
- **Multijugador (hub)**: "Crear partida" / "Conectarse" / volver.
- **Crear partida**: campo de nombre del host + reglas (victoria, turnos máx,
  tiempo por turno, tamaño de mapa, puerto) → botón "Esperar conexiones" → pasa
  a la **pantalla de espera**.
- **Conectarse**: campo de nombre + IP + puerto → "Conectar".
- **Sala de espera / lobby**: muestra el estado ("Esperando jugador…",
  "Jugador *X* conectado"), aviso al host cuando entra el cliente, y el botón
  **"Listo"** de cada uno. Cuando ambos están listos → arranca `GameScreen` en
  modo red. El host puede **"Cancelar"** (diálogo de confirmación: "se
  desconectará al jugador externo").
- **Nombres**: `Player.name` ya existe; se rellena con lo ingresado.

`GameScreen` en modo red:

- En lugar de `self.ais`, un **`NetPlayer`** que aporta las órdenes del par
  (las que llegan por `ORDERS`). `end_turn` deja de llamar a la AI y pasa a:
  enviar las órdenes locales, esperar las del par, y recién ahí `run_turn`.
- **Chat en el sidebar**: zona de mensajes + input de texto; envía/recibe
  `CHAT`. Historial scrolleable corto.
- **Reloj de turno** visible si la regla está activa.
- **Indicador de conexión** (conectado / latencia / "esperando rival").
- Reorganizaciones (fusión/división) pasan a ser **diferidas vía órdenes** en
  modo red (sección 3, punto 2).

Persistencia: el nombre del jugador y el último IP/puerto usados pueden
guardarse en `settings.json` (como las prefs de música/video).

---

## 9. Errores, desconexión y cierre

- **Desconexión** (socket cae, timeout de PING): la partida termina con un aviso
  ("El rival se desconectó"); se vuelve al menú. v0.4.0 no reconecta.
- **Host cancela**: envía `BYE(motivo)`, el cliente vuelve al menú con aviso.
- **Handshake fallido** (versión/protocolo/config distintos): rechazo con motivo
  claro en la pantalla de conexión.
- **Mismatch de HASH**: STATE_SYNC del host; si se repite, se aborta con aviso
  (señal de bug de determinismo — quedaría logueado).
- **Timeout de turno**: lo maneja el reloj de turno (auto-envío), no es error.

---

## 10. Tests

- `net/protocol`: round-trip de cada mensaje (serializa→deserializa→igual),
  framing (mensajes partidos/concatenados en el stream).
- `net/orders_codec`: round-trip de cada tipo de `Order`.
- **Determinismo lockstep**: dos `Game.new` con la misma seed + mismas órdenes
  por N turnos → mismo `to_dict()` y mismo hash (test puro, sin sockets).
- `net/session`: máquina de estados (transiciones válidas/ inválidas),
  filtrado de órdenes por dueño, detección de mismatch → STATE_SYNC.
- `transport`: par de sockets en `localhost` (loopback) intercambiando mensajes
  en un test de integración acotado (con timeout).
- El test de humo `test_core_does_not_import_pygame` se extiende para verificar
  que **`wom.net` tampoco importa pygame**.
- Reorganización vía órdenes ya cubierta por la AI; se agrega el caso de que el
  humano en red use `Merge/SplitArmyOrder`.

---

## 11. Plan de implementación por fases

| Fase | Contenido | Validación | Estado |
|---|---|---|---|
| MP1 | `net/protocol` + `net/orders_codec` + `net/state_hash`; test de determinismo lockstep | tests puros verdes | ✅ hecho |
| MP2 | `net/transport` (TCP, hilo lector, framing) + `net/session` (máquina de estados, handshake, lobby) + `net/config_fingerprint` | test loopback host↔cliente | ✅ hecho |
| MP3 | UI: menú Multijugador, crear/conectar, sala de espera, ready, cancelar (`ui/multiplayer_screen.py` + `net/rules.py`) | conexión LAN real, lobby funcional | ✅ hecho |
| MP4 | `GameScreen` en red: `NetGame` (lockstep), intercambio de órdenes, `run_turn` sincronizado, animación local, reorg diferida (`TransferTroopsOrder`/`SplitArmyOrder`) | partida completa 2 humanos LAN | ✅ hecho |
| MP5 | Reglas (turnos máx vía `Game.turn_limit`, reloj de turno con auto-envío), chat en sidebar, nombres, indicadores de conexión | partida con reglas + chat | ✅ hecho |
| MP6 | Robustez: desconexión, STATE_SYNC, validación de órdenes, mensajes de error; bump a 0.4.0 | pruebas de caída/cancelación | pendiente |

Cada fase mantiene el core sin tocar (salvo, si hace falta, exponer el límite de
turnos como parámetro — cambio menor y testeado).

---

## 12. Riesgos y mitigaciones

| Riesgo | Impacto | Mitigación |
|---|---|---|
| Divergencia de determinismo entre máquinas | Alto | RNG único + config con huella + HASH por turno + STATE_SYNC de respaldo |
| Reorganización inmediata rompe el lockstep | Medio | En red se difiere vía `Merge/SplitArmyOrder` (ya soportadas) |
| Bloqueo del render por la red | Medio | Hilo lector + cola drenada por frame; nada bloqueante en el loop |
| NAT / firewall en LAN | Bajo | Documentar puerto + port-forward; alcance LAN/IP directa |
| Cliente buggeado/malicioso | Bajo | Validación de órdenes por dueño; host árbitro |
| Scope creep (reconexión, >2 jugadores) | Medio | Explícitamente fuera de v0.4.0 |
