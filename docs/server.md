# WOM — Diseño del Servidor Online

> **Versión mayor (objetivo 0.7.0).** Hasta hoy el multijugador es **LAN/IP
> directa**: un jugador hospeda (host = jugador 0) y es a la vez **autoridad y
> relay** de una única partida (`docs/multiplayer.md`). Esta versión agrega un
> **servidor dedicado stand-alone**: un proceso que se instala en un host abierto
> a Internet, mantiene un **lobby** de jugadores con chat, **aloja varias
> partidas a la vez** y actúa como **autoridad** de cada una (resuelve los turnos
> y las batallas igual que hoy hace el host). El cliente es el mismo juego, con
> una rama nueva en "Multijugador" para **partidas por Internet** (navegador de
> servidores) además de las **LAN** actuales.

Documento derivado del pedido de "Server para juego online". Define la relación
con el multiplayer LAN actual, la arquitectura, el modelo de dos niveles
(lobby + partidas), el protocolo, la concurrencia, las mitigaciones anti-DDOS,
el despliegue (incluido el manual de firewall en Linux), la UI del cliente, los
tests y un plan por fases.

**Decisiones ya tomadas con el usuario:**

1. **El servidor es la autoridad** que resuelve los turnos/batallas (no un relay
   puro). Corre `Game.run_turn` por cada partida, valida las órdenes por dueño,
   chequea hashes y resincroniza — exactamente el rol que hoy cumple el host,
   pero **sin ser jugador** y multiplicado por N partidas. Los clientes siguen
   corriendo su propia simulación local para animar (lockstep).
2. **Se entrega primero este documento de diseño**; la implementación va por
   fases (sección 12) una vez revisado.

---

## 1. Alcance

Incluye:

- **Servidor dedicado** instalable **sin el resto del juego** (carpeta aparte,
  dependencias propias, sin pygame ni assets), desplegable en un host Linux.
- **Lobby**: los jugadores se conectan, ven la lista de jugadores (libres /
  en partida), **chatean** en un canal general, y ven la **lista de partidas**
  disponibles (cupo, estado, mapa).
- **Entrada libre o con contraseña** única definida por el dueño del servidor en
  un archivo de configuración.
- **Varias partidas simultáneas** en el mismo proceso, independientes entre sí.
- **Crear partida**: un jugador define cupo (2..`MAX_PLAYERS`), origen del mapa
  (escenario `.wom` predefinido o aleatorio) y reglas; la partida aparece
  **disponible** para el resto al instante.
- **Unirse**: si la partida tiene cupo libre el jugador entra; si está **llena**
  el servidor lo rechaza con un aviso ("la partida está llena").
- **El servidor atiende las batallas** igual que el host actual (autoridad
  lockstep por partida).
- **Mitigaciones anti-DDOS** razonables en stdlib + **manual de firewall Linux**
  (apertura de puerto y rate-limiting a nivel SO).
- **Cliente**: nueva rama en "Multijugador" que separa **LAN** (lo actual) de
  **Internet** (servidores); administración de una **lista de servidores**
  (agregar/editar/borrar, persistida); vista de lobby con jugadores + chat +
  partidas; crear / unirse; y mostrar **errores/avisos** que mande el servidor.

**Fuera de alcance (posible más adelante):**

- Cuentas de usuario, login persistente, ranking/ELO, amigos. La identidad es
  solo un **nick** por sesión (+ contraseña única del servidor, opcional).
- Matchmaking automático, NAT traversal / hole punching (se asume IP/puerto
  públicos o port-forward; ver manual).
- Espectadores, repeticiones server-side, torneos.
- Cifrado/TLS de la conexión (se asume red semi-confiable; ver §10 sobre TLS por
  túnel como opción de despliegue).
- Guardar/cargar una partida en red a mitad de juego (igual que en LAN).

---

## 2. Relación con el multiplayer LAN actual (qué se reaprovecha)

El multiplayer LAN (v0.4.0) ya implementó **topología estrella con el host como
autoridad + relay** y **lockstep determinista**. El servidor dedicado **es el
mismo patrón**, con dos diferencias:

| | LAN actual (host) | Servidor dedicado |
|---|---|---|
| Quién es autoridad/relay | El jugador 0 (host) | El proceso servidor |
| ¿La autoridad juega? | Sí, es el jugador 0 | **No**, es player-less |
| Partidas simultáneas | 1 | N |
| Capa de lobby sobre las partidas | No (va directo a una sala) | **Sí** (lobby global + navegador de partidas) |

Por eso **se reaprovecha casi todo lo de `wom/net/`**:

- `protocol.py` — framing por longitud (`FrameDecoder`, `encode_frame`,
  `MAX_FRAME_SIZE`) y el catálogo de mensajes de **partida**
  (`Orders`/`TurnOrders`/`Hash`/`StateSync`/`Ready`/`Start`/`GameSetup`/`Chat`/
  `Bye`/`Ping`/`Pong`). Se **agregan** mensajes de **lobby** (§6).
- `transport.py` — `Connection` (hilo lector + cola, `send` thread-safe, framing)
  y `Server` (acepta en segundo plano, sigue aceptando durante la partida para
  reconexión). Se reusan sin cambios.
- `orders_codec.py`, `state_hash.py`, `config_fingerprint.py`, `rules.py` — sin
  cambios. El fingerprint sigue cubriendo `classes.json`+`game.json` (la AI de
  relleno corre solo en el servidor, así que `ai.json` no necesita coincidir).
- La lógica **host-side** de `HostSession` (asignar ids, juntar órdenes,
  validar por dueño, repartir el bundle, hash/StateSync) y de `NetGame`
  (`canonical_order`, `_apply_turn`, `_validate_owned`, IA de relleno de
  ausentes, reconexión con `live_state_provider`) se **generaliza a player-less**
  y se mueve/comparte con el servidor (§4). El **cliente no cambia su rol**:
  `ClientSession` + `NetGame(is_host=False)` ya hacen exactamente lo que un
  jugador debe hacer contra una autoridad externa.

**Conclusión:** en modo servidor **todos los humanos son clientes**; el servidor
es el host/autoridad. El split `is_host` que ya existe en `NetGame` mapea directo
(servidor = lado host pero sin órdenes locales propias).

---

## 3. Arquitectura

### 3.1 Capas y separación

Se mantiene la regla del proyecto: **lo que el servidor usa no importa pygame**.
Las capas puras (`core`, `net`, `persistence`, `mapgen`, `ai`, `llm`) **no usan
pygame ni dependencias de terceros — son stdlib pura**. El servidor se apoya solo
en ellas, así que:

- **El servidor es stdlib-only**: sin pygame, sin assets, sin terceros. Deploy
  mínimo y **superficie de ataque chica**. Empaquetable incluso como binario
  único con PyInstaller si se quiere.

```
┌──────────────────────────────────────────────────────────┐
│ server/  (carpeta separada, RUNNABLE del servidor)        │  ← stdlib
│   main.py · loop de sockets · config · logging · señales  │
├──────────────────────────────────────────────────────────┤
│ wom/net/  protocolo, transporte, sesión, lockstep         │  ← SIN pygame
│   + lobby.py (estado del lobby)                            │
│   + match.py (MatchRunner autoritativo player-less)        │
├──────────────────────────────────────────────────────────┤
│ wom/core/ (incl. mapgen.py) · wom/persistence/ · wom/ai/  │  ← SIN pygame
└──────────────────────────────────────────────────────────┘
        ▲
        │ (mismas dataclasses de mensaje y codecs)
        ▼
┌──────────────────────────────────────────────────────────┐
│ wom/ui/  navegador de servidores + lobby + GameScreen(net)│  ← pygame
└──────────────────────────────────────────────────────────┘
```

**Lógica pura, testeable headless, dentro de `wom/net/`** (cubierta por el test
de humo "no importa pygame"):

- `wom/net/lobby.py` — `LobbyServer`: máquina de estado del lobby (conexiones,
  handshake/contraseña, roster de jugadores, chat global, catálogo de partidas,
  crear/unirse/salir). No toca sockets directamente: opera sobre `Connection`s,
  igual que `HostSession`.
- `wom/net/match.py` — `MatchRunner`: una partida server-side. Es el `NetGame`
  del host **generalizado a player-less** (no aporta órdenes propias; solo junta
  las de los jugadores, arma el bundle, corre `run_turn`, hash/StateSync, IA de
  relleno y reconexión). Reusa `canonical_order`/`state_digest`/`orders_codec`.

**Runnable del servidor, en `server/`** (carpeta separada, lo que se instala):

- `server/main.py` — punto de entrada: parsea config, abre el `socket` de
  escucha (reusa `transport.Server`), corre el **loop principal** (drena
  conexiones y tickea lobby + matches), maneja señales (`SIGINT`/`SIGTERM`) y
  logging.
- `server/config.py` — carga `server.toml`/`server.ini` (puerto, contraseña,
  límites, carpeta de escenarios, logging).
- `server/requirements.txt` — vacío o casi (stdlib). Sirve para fijar la versión
  de Python y, si se decide, `tomli` para Python <3.11 (3.13 ya trae `tomllib`).
- `server/README.md` (o `docs/server_deploy.md`) — manual de instalación,
  apertura de puerto y firewall (§11).

> **Por qué la lógica va en `wom/net/` y el runnable en `server/`:** así la parte
> con reglas (lobby, lockstep player-less) es **pura y se testea headless** con
> el resto de `net/`, mientras que `server/` queda como una cáscara delgada de
> I/O. Para desplegar el servidor se copia el subárbol puro de `wom/` + `data/`
> + `server/` (un script `tools/pack_server.py` arma ese paquete mínimo; ver
> §11).

### 3.2 Modelo de dos niveles

```
        conectar al servidor
                │  (Join + contraseña opcional)
                ▼
        ┌───────────────┐   crear / unirse partida   ┌──────────────┐
        │     LOBBY      │ ─────────────────────────► │   PARTIDA    │
        │ jugadores +    │                            │ (lockstep,   │
        │ chat + lista   │ ◄───────────────────────── │  server =    │
        │ de partidas    │   fin / abandono           │  autoridad)  │
        └───────────────┘                            └──────────────┘
```

- **Nivel lobby:** una `Connection` autenticada pertenece al `LobbyServer`. Ve
  el roster (jugadores libres / en partida) y el catálogo de partidas, chatea en
  el canal general, crea o se une a una partida.
- **Nivel partida:** al unirse, la `Connection` se "presta" a un `MatchRunner`.
  Mientras la partida corre, los mensajes de partida (`Orders`/`Hash`/`Chat`…)
  se enrutan a ese `MatchRunner`; los de lobby siguen atendiéndose (chat global,
  ver lista). Al terminar/abandonar, la conexión **vuelve al lobby**.

Cada partida server-side reusa el rol de host del lockstep, así que **dentro de
una partida valen las mismas garantías de determinismo** documentadas en
`docs/multiplayer.md` §3.

---

## 4. El servidor como autoridad (player-less)

El `NetGame` del host hoy hace `bundle = {self.human_id: self._local_orders,
**client_orders}`: siempre inyecta sus órdenes locales. El servidor **no tiene
órdenes propias**. `MatchRunner` es la versión player-less:

- No hay `human_id` ni `_local_orders`. El bundle son **solo** las órdenes de los
  jugadores: `bundle = {pid: client_orders[pid] for pid in jugadores_presentes}`.
- Resuelve cuando llegaron las órdenes de **todos los jugadores presentes** (los
  ausentes los cubre la IA, igual que hoy: `ai_factory` ya existe en `NetGame`).
- Corre `run_turn(canonical_order(...))`, calcula el `state_digest` **autoritativo**
  y reparte `TurnOrders`. Cada cliente corre el mismo bundle, manda su `Hash`, y
  el servidor resincroniza con `StateSync` al que diverja (red de seguridad ante
  bug de determinismo).
- **No necesita renderizar nada.** El servidor corre la simulación "a ciegas"
  para ser la autoridad; los transitorios de animación (`last_moves`/`last_clashes`)
  los reconstruye cada cliente localmente desde su propio `run_turn`.

Esto es una **generalización**, no una reescritura: se factoriza la parte
player-less de `NetGame`/`HostSession` y el host LAN actual pasa a ser "un
`MatchRunner` que además es el jugador 0". (Alternativa más conservadora:
mantener `NetGame` como está y que el servidor instancie un `MatchRunner` nuevo
que comparta helpers — `canonical_order`, `_validate_owned`, IA de relleno. Se
decide en la fase de implementación; el doc no obliga a refactorizar el host
LAN.)

### Validación por dueño = anti-cheat

`_validate_owned`/`_owned_by` (ya en `NetGame`) descartan órdenes de un cliente
que toquen ejércitos/fuertes ajenos. Al ser el servidor la autoridad, **un
cliente no puede mover piezas de otro ni inyectar estado**: solo manda sus
órdenes, el servidor las filtra y resuelve. Es la misma defensa de hoy, ahora
sin confiar en ninguna máquina cliente.

---

## 5. Mapas de las partidas

Al crear una partida el creador elige el **origen del mapa**, resuelto siempre en
el servidor (autoridad):

- **Escenario predefinido (`.wom`)**: el servidor lista los `.wom` de su carpeta
  de escenarios (`scenario.list_maps` sobre una carpeta configurable; por defecto
  `data/scenarios/` + una carpeta del operador). `build_game(load_scenario(path))`
  arma el `Game`. Honra IA/victoria del escenario si los trae.
- **Aleatorio**: el servidor genera con `generate_map(MapParams(seed=...))`. La
  **seed la elige el servidor** (no el cliente) y queda horneada en el estado
  inicial.

En ambos casos el servidor construye el `Game` autoritativo y manda `GameSetup`
(con `state = game.to_dict()` y el `human_id` de cada jugador) — idéntico a hoy,
solo que el "setup_provider" vive en el servidor. La cantidad de jugadores del
mapa y el cupo de la partida deben ser coherentes (un escenario de 2 jugadores
no abre cupo para 4); el servidor valida y, si no, rechaza la creación con aviso.

---

## 6. Protocolo

Se **reusa el framing** (`uint32 BE longitud + JSON UTF-8`, `FrameDecoder`,
`MAX_FRAME_SIZE`) y **todos los mensajes de partida**. Se **agregan** mensajes de
**lobby** (nuevas dataclasses en `protocol.py`, registradas en `_MESSAGE_TYPES`):

### Mensajes nuevos de lobby

| Mensaje | Dirección | Contenido | Cuándo |
|---|---|---|---|
| `Join` | cliente→servidor | versión WOM, versión protocolo, huella config, nick, `password` | al conectar |
| `Welcome` (reuso/ext.) | servidor→cliente | acepta/rechaza (+motivo), nombre del servidor, `your_id` de lobby | respuesta a `Join` |
| `LobbyState` | servidor→clientes | jugadores `[id, nick, estado(libre/en-partida), match_id?]` + partidas `[match_id, nombre, cupo, ocupados, estado, mapa]` | al cambiar el lobby |
| `CreateMatch` | cliente→servidor | nombre, `max_players`, origen de mapa (`scenario`/`random` + ref), reglas (`MatchRules`) | el jugador crea partida |
| `JoinMatch` | cliente→servidor | `match_id` | el jugador se une |
| `LeaveMatch` | cliente→servidor | — | volver al lobby antes de empezar |
| `MatchJoined` | servidor→cliente | `match_id`, `your_seat` (id de jugador en esa partida), roster de la sala | tras unirse OK |
| `LobbyChat` | ambos | nick, texto, ts | chat **global** del lobby |
| `Error` | servidor→cliente | `code`, `message` | rechazo de acción (llena, contraseña, etc.) |

### Mensajes de partida (ya existen, se reusan tal cual)

`GameSetup`, `Ready`, `Lobby` (roster de la **sala** de una partida), `Start`,
`Orders`, `TurnOrders`, `Hash`, `StateSync`, `Chat` (chat **de la partida**),
`Ping`/`Pong`, `Bye`.

> **Lobby global vs sala de partida:** `LobbyChat`/`LobbyState` son del lobby
> (todos los conectados). `Chat`/`Lobby`/`Ready` son **dentro de una partida**
> (solo sus jugadores). Se distinguen por tipo de mensaje, no por canal.

### Errores con código

`Error.code` permite que el cliente muestre el aviso correcto y, si hace falta,
reaccione (volver al navegador, re-pedir contraseña): `WRONG_PASSWORD`,
`MATCH_FULL`, `MATCH_GONE`, `VERSION_MISMATCH`, `CONFIG_MISMATCH`, `NAME_TAKEN`,
`RATE_LIMITED`, `SERVER_FULL`, `INVALID`. El texto va en español listo para HUD.

---

## 7. Máquinas de estado

### 7.1 Servidor — por conexión

```
            Join + (password)            crea/une
  CONNECTING ───────────────► IN_LOBBY ───────────► IN_MATCH
      │  rechazo (Error+cerrar)   │  ▲                  │
      ▼                           │  └─ fin/abandono ───┘
   CLOSED ◄───────────────────────┘  (caída → MatchRunner la cubre con IA)
```

- `CONNECTING`: esperando `Join`. **Timeout de handshake** (p. ej. 10 s) cierra
  la conexión si no llega (anti-slowloris). Valida versión/protocolo/config/
  contraseña; rechazo → `Error` + `Bye` + cerrar.
- `IN_LOBBY`: la conexión está en el `LobbyServer`. Recibe `LobbyState`,
  `LobbyChat`; puede `CreateMatch`/`JoinMatch`.
- `IN_MATCH`: la conexión la atiende un `MatchRunner`. Sigue recibiendo
  `LobbyState`/`LobbyChat` (marca al jugador como "en partida"). Al terminar o
  abandonar, vuelve a `IN_LOBBY`.

### 7.2 Servidor — por partida (`MatchRunner`)

`OPEN` (en sala, esperando jugadores + readies) → `PLAYING` (lockstep) →
`FINISHED`/`ABANDONED`. Mientras está `OPEN` aparece en `LobbyState` como
**disponible**; al llenarse o arrancar, como **en curso**. Una partida vacía
(todos se fueron) se **recicla** y desaparece del catálogo.

### 7.3 Cliente

Se agrega un nivel de **navegación de servidores** y **lobby remoto** antes de la
sala de partida; una vez dentro de la partida, **el cliente ya no distingue
LAN de Internet**: corre `GameScreen(net=…)` igual que hoy.

```
  Multijugador
    ├── LAN (actual: crear/conectar por IP)
    └── Internet
          └── Navegador de servidores  ──conecta──►  Lobby remoto
                 (lista guardada,                      (jugadores+chat+partidas)
                  agregar/editar/borrar)                   │ crear/unirse
                                                           ▼
                                                       Sala (Ready) ──Start──► GameScreen(net)
```

---

## 8. Flujo de uso (end to end)

1. **Conectar.** El cliente elige un servidor de su lista (host:puerto), manda
   `Join` con nick + huella de config + contraseña (si el server la pide). El
   servidor valida y responde `Welcome` (o `Error`). OK → entra al lobby y
   recibe `LobbyState`.
2. **Lobby.** Ve jugadores (libres / en partida), chatea (`LobbyChat`) y ve las
   partidas disponibles. Puede **crear** (`CreateMatch`: cupo, mapa, reglas) — la
   partida aparece en el `LobbyState` de todos al instante — o **unirse**
   (`JoinMatch`).
3. **Sala.** Al unirse, el servidor crea/asocia un `MatchRunner`, le asigna un
   `seat` (id de jugador) y manda `MatchJoined` + `GameSetup` cuando la sala se
   completa (igual que `HostSession._maybe_send_setup` hoy). Todos marcan
   `Ready`; cuando están todos, el servidor manda `Start`.
4. **Jugar.** A partir de `Start` es **el lockstep actual**, con el servidor de
   autoridad: cada cliente manda `Orders`, el servidor junta+valida+reparte
   `TurnOrders`, todos corren `run_turn`, mandan `Hash`, el server resincroniza
   si hace falta. Reloj de turno, chat de partida, reorg diferida: idénticos a
   LAN.
5. **Caída/reconexión.** Si un jugador se cae, el `MatchRunner` lo cubre con IA
   (`PlayerLeft` → `ai_factory`) y deja el slot reservado; al reconectarse (desde
   el navegador, reentrando a esa partida) el servidor le manda el estado vivo
   (`live_state_provider` → `GameSetup`+`Start`) y retoma su lugar
   (`PlayerRejoined`). **Esto ya está implementado** en `NetGame`/`HostSession`;
   se hereda.
6. **Fin.** Al terminar la partida (victoria o límite), los clientes vuelven al
   **lobby** (no al menú principal). El `MatchRunner` pasa a `FINISHED` y se
   recicla.

---

## 9. Concurrencia (varias partidas a la vez)

Modelo simple y robusto, reusando lo que ya hay:

- **Un hilo aceptador** (el de `transport.Server`, ya existe) deja las conexiones
  nuevas en una cola.
- **Un hilo lector por conexión** (el de `Connection`, ya existe) reensambla
  mensajes en su `queue.Queue`. La red nunca bloquea la lógica.
- **Un único loop principal** (hilo del servidor) que tickea a frecuencia fija
  (p. ej. 30–60 Hz): en cada tick drena `poll()` de cada conexión, pasa los
  mensajes al `LobbyServer` o al `MatchRunner` que corresponda, y avanza cada
  `MatchRunner.update()`. **Sin hilo por partida**: el costo de correr `run_turn`
  para un puñado de partidas por turnos es trivial (microsegundos), así que un
  loop secuencial alcanza y evita locks entre partidas.

Por qué no un hilo por partida: agrega locks (estado compartido del lobby,
roster) y complejidad sin necesidad — WOM es por turnos, no hay presión de
tiempo real. Si en el futuro hiciera falta escalar, las partidas son
independientes y se podrían shardear en procesos, pero eso queda fuera de alcance.

---

## 10. Anti-DDOS, robustez y seguridad

Mitigaciones **en stdlib** (capa de aplicación):

- **Frames acotados.** `MAX_FRAME_SIZE` ya corta frames gigantes; se baja el cap
  para mensajes de **lobby** (no llevan estado de juego) y se deja amplio solo
  para `GameSetup`/`StateSync`. Un frame fuera de rango → cerrar la conexión.
- **Timeout de handshake.** Una conexión que no manda `Join` válido en N segundos
  se cierra (anti-slowloris / conexiones zombi).
- **Límite de conexiones**: total (`max_connections`) y **por IP**
  (`max_connections_per_ip`) — corta floods de un mismo origen. Al superar el
  total se rechaza con `Error(SERVER_FULL)` antes de asignar estado.
- **Rate-limit de mensajes por conexión** (token bucket: X msgs/seg, ráfaga Y).
  Exceso → `Error(RATE_LIMITED)` y, si reincide, cierre. Cubre spam de chat y de
  acciones de lobby.
- **Caps de tamaño/forma**: largo de nick, largo de texto de chat, cantidad de
  partidas por jugador (1 a la vez), cupo máximo (`MAX_PLAYERS`). Entrada
  malformada → `Error(INVALID)`, nunca excepción no manejada.
- **Contraseña única** (opcional) chequeada **antes** de asignar cualquier estado
  más allá del handshake: con `require_password=true`, sin la correcta no se
  entra al lobby.
- **Aislamiento de fallos**: una excepción dentro de un `MatchRunner` termina
  **esa** partida con aviso a sus jugadores (`Bye`/`Error`) y se loguea, **sin
  tumbar** el servidor ni las otras partidas (try/except por partida en el loop).

Lo que **no** resuelve la capa de aplicación (queda para el SO/firewall, ver
§11): floods SYN, amplificación, saturación de ancho de banda. Para eso el manual
documenta **rate-limiting con nftables/iptables** y opcionalmente **fail2ban**.

**Sobre cifrado:** el protocolo es JSON en claro. Para Internet público se
recomienda en el manual exponer el puerto **detrás de un túnel/redirección
cifrada** (WireGuard, o `stunnel`/SSH tunnel) si se quiere confidencialidad; el
juego no maneja datos sensibles más allá del nick, así que TLS nativo queda fuera
de alcance de esta versión (anotado como mejora futura).

---

## 11. Despliegue (carpeta separada + manual de firewall)

### 11.1 Paquete mínimo del servidor

`tools/pack_server.py` arma una carpeta autosuficiente con **solo lo puro**:

```
wom-server/
├── wom/                 # SOLO subpaquetes puros: core (incl. mapgen),
│                        # net, persistence, ai  (sin ui/, sin pygame)
├── data/
│   ├── config/          # classes.json, game.json, ai.json
│   └── scenarios/       # .wom predefinidos (+ los que sume el operador)
├── server/              # main.py, config.py, README
├── server.toml          # config del operador (ver abajo)
└── requirements.txt     # vacío/stdlib (Python 3.13)
```

Se corre con `python -m server` (o un binario PyInstaller). Como todo es stdlib,
no hace falta compilar nada nativo.

### 11.2 Configuración (`server.toml`)

```toml
[server]
host = "0.0.0.0"
port = 50000
name = "Mi servidor WOM"

[auth]
require_password = false
password = ""            # se ignora si require_password = false

[limits]
max_connections = 200
max_connections_per_ip = 8
max_matches = 20
handshake_timeout = 10   # segundos
msg_rate = 30            # mensajes/seg por conexión
msg_burst = 60

[maps]
scenarios_dir = "data/scenarios"
allow_random = true

[logging]
level = "info"
file = "wom-server.log"
```

### 11.3 Manual de firewall (Linux) — incluido en `docs/server_deploy.md`

El manual cubre, paso a paso:

1. **Instalar** (copiar `wom-server/`, Python 3.13, `python -m server`).
2. **Servicio systemd** (`wom-server.service`) para que arranque solo y se
   reinicie ante caída (con `Restart=on-failure`, usuario sin privilegios).
3. **Abrir el puerto** según el firewall:
   - **UFW**: `sudo ufw allow 50000/tcp` (+ `ufw limit` para rate-limit básico).
   - **firewalld**: `firewall-cmd --add-port=50000/tcp --permanent && firewall-cmd --reload`.
   - **nftables/iptables**: regla de `accept` al puerto + ejemplo de
     **rate-limit** (`limit rate`/`hashlimit` por IP) y `ct state` para mitigar
     floods.
4. **Reenvío de puerto** en el router (si está detrás de NAT doméstico) y cómo
   averiguar la IP pública.
5. **(Opcional) fail2ban** con un filtro sobre el log del servidor para banear
   IPs que disparen muchos `RATE_LIMITED`/handshakes fallidos.
6. **(Opcional) túnel cifrado** (WireGuard/SSH) para confidencialidad.
7. **Verificación**: conectar un cliente desde otra red; checklist de problemas
   comunes (puerto cerrado, IP equivocada, contraseña, versión/config distinta).

---

## 12. Plan de implementación por fases

| Fase | Contenido | Validación |
|---|---|---|
| **S0** | Este documento + esqueleto: carpeta `server/`, `pack_server.py`, smoke test "el servidor no importa pygame" | doc revisado; `python -m server --check` levanta y cierra |
| **S1** | Protocolo de lobby: nuevas dataclasses (`Join`/`LobbyState`/`CreateMatch`/`JoinMatch`/`MatchJoined`/`LobbyChat`/`Error`) + round-trip y framing | tests de protocolo verdes |
| **S2** | `wom/net/lobby.py` (`LobbyServer`): handshake+contraseña, roster, chat global, catálogo de partidas, crear/unirse/salir, errores. Headless | tests de la máquina de estado del lobby |
| **S3** | `wom/net/match.py` (`MatchRunner`): partida autoritativa player-less (reusa `canonical_order`/`state_hash`/`orders_codec`, IA de relleno, reconexión). Headless | test de partida completa server↔N clientes en loopback, en sync |
| **S4** | `server/main.py`: loop principal, varias partidas simultáneas, config (`server.toml`), logging, señales, aislamiento de fallos por partida | test de integración: 2 partidas a la vez en loopback |
| **S5** | Anti-DDOS: timeout de handshake, límites de conexión/IP, rate-limit por conexión, caps de tamaño | tests de límites (rechazos correctos) |
| **S6** | UI cliente — navegador de servidores: lista guardada en `settings.json`, agregar/editar/borrar, conectar, mostrar `Error` del server | conexión real a un servidor local |
| **S7** | UI cliente — lobby remoto: jugadores+estado, chat global, catálogo de partidas, crear/unirse → entra a la sala → `GameScreen(net)` | partida completa por "Internet" (loopback/LAN) |
| **S8** | Robustez UI: volver al lobby al terminar/abandonar, reconexión a una partida, avisos; bump a 0.7.0 | partida multi-jugador de punta a punta con caída/reconexión |
| **S9** | Despliegue: `docs/server_deploy.md` (systemd + firewall UFW/nftables/fail2ban), `server.toml` de ejemplo, `pack_server.py` final | desplegar en un host Linux y conectar desde afuera |

Cada fase mantiene el core sin tocar y el test de humo "las capas del servidor no
importan pygame" en verde.

---

## 13. Tests

- **Protocolo de lobby**: round-trip de cada mensaje nuevo + framing
  (partido/concatenado), reusando el patrón de `test_net_protocol`.
- **`LobbyServer`**: handshake OK/rechazo (versión/protocolo/config/contraseña),
  roster, crear/unirse/llena (`MATCH_FULL`), salir, chat global. Headless con
  pares de `Connection` en loopback (como `test_net_*` hoy).
- **`MatchRunner`**: determinismo lockstep server↔N clientes por M turnos →
  mismo `to_dict()`/hash que correr `run_turn` directo; validación por dueño;
  IA de relleno de ausentes; reconexión con estado vivo. Extiende
  `test_net_lockstep`.
- **Concurrencia**: dos `MatchRunner` simultáneos no se interfieren (estado
  independiente).
- **Anti-DDOS**: timeout de handshake cierra; exceso de conexiones/IP rechaza;
  rate-limit dispara `RATE_LIMITED`; frame fuera de rango cierra.
- **Humo**: `wom.net.lobby`, `wom.net.match` y `server` **no importan pygame**
  (se extiende `test_smoke`).
- **UI** (headless `SDL_VIDEODRIVER=dummy`): el navegador de servidores
  agrega/edita/borra y persiste; la vista de lobby parsea `LobbyState`.

---

## 14. Riesgos y mitigaciones

| Riesgo | Impacto | Mitigación |
|---|---|---|
| Divergencia de determinismo entre máquinas heterogéneas | Alto | Igual que LAN: RNG único + huella de config + `Hash` por turno + `StateSync` autoritativo del servidor (ahora una autoridad neutral, no un cliente) |
| DDOS / abuso de un servidor público | Alto | Caps de conexión/IP, rate-limit, timeout de handshake, contraseña opcional, + firewall/fail2ban a nivel SO (manual) |
| Una partida tumba todo el servidor | Alto | Aislamiento por `try/except` por `MatchRunner`; una excepción termina esa partida, no el proceso |
| Acoplar el servidor a pygame/asset sin querer | Medio | Smoke test "no importa pygame" sobre `wom.net.lobby`/`match`/`server`; `pack_server.py` solo copia subpaquetes puros |
| Refactor del host LAN para hacerlo player-less rompe el modo LAN | Medio | El doc no obliga a refactorizar: `MatchRunner` puede nacer aparte compartiendo helpers; el host LAN sigue funcionando y se migra solo si conviene |
| Mapa del escenario vs cupo incoherentes | Bajo | El servidor valida al crear y rechaza con `Error(INVALID)` |
| Confidencialidad en Internet público (JSON en claro) | Bajo | Documentar túnel cifrado (WireGuard/SSH) como opción; sin datos sensibles más allá del nick |

---

## 15. Resumen

El servidor online es una **extensión natural** del multiplayer LAN: el rol de
host (autoridad + relay del lockstep) se **extrae a un proceso dedicado
player-less** que aloja **muchas partidas** bajo un **lobby con chat**. Se
reaprovecha casi todo `wom/net/` (protocolo, transporte, codecs, validación por
dueño, IA de relleno, reconexión) y el cliente apenas suma una **rama "Internet"
con navegador de servidores y vista de lobby** antes de caer en el mismo
`GameScreen(net)` de siempre. El servidor es **stdlib-only**, lo que da un deploy
mínimo y una superficie de ataque chica; las mitigaciones anti-DDOS van en la
app (caps + rate-limit) y en el firewall (manual). La pieza load-bearing —el
**determinismo lockstep**— se preserva intacta, ahora arbitrada por una autoridad
neutral.
```
