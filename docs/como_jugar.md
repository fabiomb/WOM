# Cómo jugar a WOM

Guía para jugadores nuevos. WOM es un juego de **estrategia militar 2D por turnos**:
mové tus ejércitos por un mapa, tomá fuertes y pueblos, y derrotá al rival. En
una partida normal jugás contra la IA (también hay multijugador humano vs humano).

> Atajo dentro del juego: apretá **F1** en cualquier momento para abrir una ayuda
> visual rápida con lo esencial de esta guía.

---

## 1. Lo básico

- La partida avanza por **turnos**. En tu turno das órdenes a tus ejércitos
  (moverlos, crearlos, reorganizarlos) y cuando estás listo apretás **Fin del
  turno**. Recién ahí se ejecuta todo: primero se mueven los ejércitos, después
  se libran las batallas, se capturan los sitios, los fuertes producen tropas y
  los ejércitos se recuperan.
- No das órdenes "en vivo": las **planificás** durante tu turno y se resuelven
  todas juntas al terminarlo. El movimiento y los combates se muestran animados.
- Cada jugador tiene un **color**: vos sos el **rojo**, el rival es el **azul**.
  Los sitios neutrales (sin dueño) son **grises**.
- Tu objetivo depende del **modo de victoria** elegido al crear la partida (ver
  §7). Por defecto se gana eliminando por completo al rival.

### El turno, paso a paso

El motor resuelve cada turno en este orden fijo:

1. **Órdenes** — lo que planificaste (movimientos, creación, fusión/división).
2. **Movimiento** — cada ejército avanza por su ruta hasta agotar su velocidad.
3. **Batallas** — se resuelven los choques contra ejércitos enemigos.
4. **Captura** — quien quede sobre un fuerte/pueblo enemigo o neutral lo toma.
5. **Producción** — tus fuertes acumulan tropas en su reserva.
6. **Recuperación** — los ejércitos suben XP y reabastecen comida según dónde estén.
7. **Chequeo de victoria** — se evalúa si la partida terminó.

---

## 2. El mapa y el terreno

El mapa es una grilla de casilleros (tiles). Cada tile tiene un **terreno** que
afecta cuánto cuesta moverse por él y los bonus de combate:

| Terreno      | Costo de movimiento | Notas de combate |
|--------------|---------------------|------------------|
| **Llanura**  | 1                   | Terreno abierto; favorece a caballeros y soldados. |
| **Bosque**   | 2                   | Cubre a partisanos y arqueros; estorba a la caballería. |
| **Montaña**  | 3                   | Lo mejor para arqueros y partisanos; pésimo para caballeros. |
| **Agua**     | intransitable       | Solo se cruza por puentes. |
| **Puente**   | 1                   | Cruza el agua, pero es un **túnel de un solo eje**. |

**Puentes:** un puente horizontal solo conecta este–oeste y uno vertical solo
norte–sur. No se puede entrar ni salir por el costado: hay que cruzarlo derecho,
de orilla a orilla.

El costo de terreno importa porque cada ejército tiene una **velocidad** (puntos
de movimiento por turno): cruzar bosque o montaña "gasta" más, así que avanzás
menos casilleros por terreno difícil.

---

## 3. Sitios: fuertes y pueblos

Hay dos tipos de sitios que se pueden poseer, marcados con una **bandera** del
color de su dueño (gris si es neutral):

### Fuertes 🏰
Son el corazón de tu economía militar:
- **Producen tropas**: cada turno acumulan tropas en su **reserva** (hasta un
  máximo). Esas tropas no salen solas al mapa.
- Para sacarlas, seleccioná el fuerte y usá **"Crear ejército"**: nace un
  ejército nuevo con las tropas de la reserva.
- Un ejército propio parado sobre tu fuerte **se reabastece** automáticamente:
  rellena sus tropas desde la reserva hasta el tope (cuesta comida de tu stock).
- En defensa, estar sobre un fuerte da un **bonus de defensa fuerte** (×1.5).
- Un ejército parado en un fuerte **nunca se retira**: la retirada solo pasa en
  campo abierto. Para desalojar a quien defiende un fuerte hay que destruirlo.
- **Capturar un fuerte enemigo destruye su reserva.** Negar producción al rival
  es tan valioso como ganarla vos.

### Pueblos 🏘️
Son apoyo logístico:
- Aportan **comida** a tu stock cada turno (la comida alimenta a tus ejércitos).
- Un ejército propio parado en un pueblo **rellena su comida** directo, sin
  gastar stock.
- En defensa dan un bonus menor que el fuerte (×1.2).
- No producen tropas.

Tomar un sitio es simple: terminá el turno con un ejército tuyo **encima** de un
sitio enemigo o neutral y pasa a ser tuyo.

---

## 4. Los ejércitos

Un ejército es un grupo de tropas que se mueve por el mapa como una sola pieza.
Lo definen:

- **Composición**: cuántas tropas tiene de cada clase (máximo **100** en total).
- **XP (experiencia / moral)**: arranca en 100. Sube quedándose quieto y baja al
  pelear. Si llega a **0, el ejército se destruye**. La XP también multiplica su
  poder de combate, así que un ejército desgastado pega menos.
- **Comida**: de 0 a 100. Baja unos puntos cada turno y se rellena en pueblos o
  fuertes propios. Poca comida también baja su eficiencia en combate.

> En resumen: la XP es a la vez la "vida/moral" del ejército y un multiplicador
> de fuerza. Reabastecer comida y descansar para recuperar XP es parte de la
> estrategia, no un detalle.

### Velocidad
La velocidad del ejército es la de **su clase más lenta**. Un ejército mixto con
soldados (velocidad 3) y caballeros (velocidad 6) se mueve a 3: la caballería no
"corre" si va acompañada de infantería lenta.

### Reorganizar ejércitos
- **Fusionar / transferir**: con un ejército seleccionado, **Shift+clic** sobre
  otro propio aledaño abre un cuadro para pasarle tropas (todo, o solo una parte
  por clase).
- **Dividir**: con un ejército seleccionado, botón **"Dividir ejército"** (o
  tecla **D**) para separar parte de las tropas en un ejército nuevo en un tile
  libre vecino.

---

## 5. Tipos de tropa y cuándo conviene cada una

Hay cuatro clases. Cada una tiene **velocidad, ataque y defensa**, además de
bonus según el **terreno** y según **contra qué clase** pelea. No hay una clase
"mejor": el truco es usar cada una donde brilla.

| Clase            | Vel | Atq | Def | Fuerte en…            | Castiga a…            |
|------------------|----:|----:|----:|-----------------------|-----------------------|
| **Partisano**    | 4   | 4   | 3   | Bosque, montaña       | Soldado               |
| **Soldado a pie**| 3   | 6   | 6   | Llanura               | Caballero             |
| **Caballero**    | 6   | 8   | 5   | Llanura               | Arquero               |
| **Arquero**      | 3   | 7   | 3   | Montaña, bosque       | Soldado, partisano    |

### Cómo leer la tabla en contexto

- **Partisano** — barato y escurridizo. Pega flojo en campo abierto (penalizado
  en llanura) pero se crece en **bosque** (×1.5) y **montaña** (×1.3), y le gana
  bien al soldado. Ideal para hostigar y defender terreno difícil.
- **Soldado a pie** — la columna vertebral: ataque y defensa parejos y altos. Va
  cómodo en **llanura** y le gana al caballero. Es tu tropa de línea y la mejor
  para **aguantar** posiciones (gran defensa, sobre todo dentro de un fuerte).
- **Caballero** — el martillo. Mucho ataque y **velocidad 6** para cruzar el
  mapa rápido. Domina en **llanura** (×1.4) y aplasta arqueros (×1.5), pero el
  **bosque y la montaña lo frenan en seco** (×0.6 y ×0.5): nunca lo metas a
  pelear ahí. Úsalo para flanquear y cazar arqueros en campo abierto.
- **Arquero** — alto ataque, defensa frágil. Brilla en **montaña** (×1.3) y
  castiga a soldados y partisanos. Su rasgo especial: **ignora el bonus de
  fuerte** del defensor, así que es la mejor pieza para **asaltar un fuerte**
  (pelea 1:1 contra la guarnición, como si las murallas no existieran). En campo
  abierto es vulnerable a la caballería.

### Triángulo de ventajas (regla rápida)

```
   Soldado  ──vence a──▶  Caballero
      ▲                       │
      │                       │ vence a
  vence a                     ▼
   Arquero  ◀──vence a──  (y al partisano)
```

- ¿Te atacan con caballería? Respondé con **soldados**.
- ¿El rival se atrinchera con soldados? Mandá **arqueros**.
- ¿Hay arqueros sueltos en campo abierto? **Caballería** encima.
- ¿Tenés que pelear en bosque o montaña? **Partisanos y arqueros**, nunca
  caballería.

La mayoría de los ejércitos conviene que sean **mixtos**: así el bonus contra
una clase enemiga se promedia según la composición del rival, y no quedás
desnudo ante un counter.

---

## 6. Cómo funcionan las batallas

Una batalla se dispara cuando un ejército intenta **entrar al tile de un
enemigo** (los ejércitos nunca comparten casillero). Cada bando pelea desde su
propio tile, con el bonus de su propio terreno; el defensor suma además el bonus
de fuerte o pueblo si está parado en uno.

El sistema calcula el **poder** de cada bando combinando:

- Cantidad de tropas × su ataque (atacante) o defensa (defensor).
- Bonus contra las clases enemigas presentes.
- Bonus del terreno donde está cada bando.
- Estado del ejército: **comida** y **XP** (cuanto más bajos, menos poder).
- Bonus de defensa por fuerte (×1.5) o pueblo (×1.2) para quien defiende.
- Un pequeño factor de azar.

Según la **proporción de poderes** el choque puede terminar en victoria clara,
retirada de uno de los bandos o empate. Toda batalla cuesta tropas y XP a ambos
lados; el que pierde o se retira paga más. Recordá: **quien defiende un fuerte
no se retira nunca** — hay que aniquilarlo para echarlo.

**Para asaltar un fuerte bien defendido, los arqueros son clave**: anulan el
bonus de las murallas para su parte del ataque.

---

## 7. Cómo se gana

El modo de victoria se elige al crear la partida:

- **Total** (por defecto): ganás cuando el rival se queda **sin ejércitos y sin
  fuertes**. Eliminación completa.
- **Banderas**: ganás cuando controlás **todas las banderas** del mapa (los
  fuertes y pueblos marcados con bandera).
- **Tiempo**: al llegar al **turno límite**, gana quien tenga más **territorio**
  (fuertes + pueblos); si hay empate, decide quién tiene más tropas.

En cualquier modo, si dejás al rival sin ejércitos ni fuertes, ganás al instante.

---

## 8. Por dónde empezar (tu primera partida)

1. **Encontrá tu ejército inicial.** Al empezar, unos anillos amarillos señalan
   dónde está. Hacé clic para seleccionarlo.
2. **Mirá el mapa.** Ubicá los fuertes y pueblos cercanos, sobre todo los
   **neutrales o grises**: son los más fáciles de tomar primero.
3. **Mandá tu ejército a un fuerte cercano.** Con el ejército seleccionado,
   hacé clic en el destino para trazar la ruta. Terminá el turno y, al llegar y
   quedarte encima, lo capturás.
4. **Producí tropas.** Una vez que tengas un fuerte, cada turno acumula tropas en
   su reserva. Seleccionalo y usá **"Crear ejército"** para sacarlas al mapa.
5. **Cuidá tu economía.** Tomá **pueblos** para tener comida y no quedarte sin
   reabastecimiento. Reabastecé ejércitos gastados parándolos en tus fuertes/pueblos.
6. **Armá ejércitos con criterio.** Mezclá clases y mandá la tropa correcta al
   terreno correcto (caballería en llanura, partisanos/arqueros en bosque y
   montaña, arqueros para asaltar fuertes).
7. **Defendé tus fuertes.** Un ejército sobre tu fuerte no se retira y defiende
   con bonus: es tu mejor posición para aguantar un ataque.
8. **Avanzá hacia el rival.** Cuando tengas ventaja, presioná sus fuertes para
   cortarle la producción y cumplir la condición de victoria.

---

## 9. Controles

| Acción                              | Cómo |
|-------------------------------------|------|
| Seleccionar ejército / fuerte       | Clic izquierdo |
| Trazar ruta (con ejército elegido)  | Clic en el destino (clics sucesivos agregan tramos) |
| Confirmar ruta / soltar selección   | Clic en el ejército, doble clic en el destino, o ESC |
| Cancelar ruta                       | Clic derecho |
| Fusionar / transferir tropas        | Shift+clic en dos ejércitos propios aledaños |
| Dividir ejército                    | Botón "Dividir ejército" o tecla **D** |
| Crear ejército (fuerte elegido)     | Botón "Crear ejército" |
| Fin del turno                       | Botón "Fin del turno" o **Enter** |
| Guardar partida                     | Botón "Guardar" o tecla **G** |
| Zoom                                | Rueda del mouse |
| Desplazar el mapa (con zoom)        | Mouse al borde, o arrastrar con botón del medio |
| Reproductor de música               | Tecla **M** |
| Esta ayuda                          | Tecla **F1** |
| Volver al menú                      | **ESC** (pide confirmación) |

¡A jugar! 🚩
