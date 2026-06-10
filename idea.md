# WOM - Juego de estrategia militar

## Idea

Tengo en mente un juego de estrategia militar multijugador y contra AI. 

La idea es contar con una cantidad limitada de clases, fuertes y pueblos y mapas.

Los objetivos serán por superioridad: tiempo (un x tiempo, quien más territorio o tropas tiene), victoria total (muerte de todas las tropas del rival y sus fuertes), captura de banderas (sea en fuertes o pueblos)

El juego será por turnos donde cada jugador da las órdenes a sus tropas indicándoles el camino a tomar. Cuando se da una batalla se podrá hacer zoom en la batalla para dirigir a las tropas, en una primera etapa (v1) no habrá zoom sino un resultado al azar con cálculo favoreciendo por tipo de tropa y terreno.

## Mapas 

El juego necesita un generador de mapas que sean fáciles de editar, en un futuro crearemos un editor de mapas, inicialmente (v1) con el generador al azar será suficiente.

Al comienzo de la partida deberá generarse el mapa en base a parámetros que debe setear el usuario: tamaño de mapa, cantidad de fuertes, cantidad de pueblos.

## Ejércitos 

Cada ejército puede contener una cantidad delimitada de soldados (ejemplo: 100), divididos en cuatro clases.

Cuatro clases:

* Partisano
* Soldado a pie
* Caballero
* Arquero

Cada uno con un set de skills diferente, se deberán crear archivos de configuración para poder definir sus características, velocidad, ataque, defensa
La velocidad de movimiento del ejército se calcula en base a la clase más lenta

Cada ejército tendrá como variables
* Cantidad de cada clase
* XP (se recupera de a 10 puntos por turno si están en fuerte, 5 puntos por turno en pueblos, 1 por turno en cualquier otro lado), XP en cero elimina el ejército derrotado, se dibuja una cruz en el mapa
* Alimentación (0 a 100, depende de la comida), con cero son menos eficientes, con 100 son eficientes al máximo

En cada batalla el número de tropas de un ejército se recalcula considerando las pérdidas

## Fuertes y pueblos 

Los fuertes producen soldados, los pueblos recursos
Los recursos son comida para el ejército

## AI 

Crear una inteligencia artificial con tres niveles de dificultad
Debe calcular distancias y estrategias
Debe poder emitir órdenes en cada turno 
Debe poder reaccionar a las acciones del usuario 
Debe estar perfectamente documentada para mejorar y modificar
Requiere parámetros ajustables para regular su funcionamiento en las pruebas

## Menú

El menú inicial de la primera versión permitirá crear un nuevo Juego
Decidir el skill del enemigo AI 
Generar el mapa y jugar 

Deberá poder guardar la partida en el estado que esté 
Deberá poder cargar partidas anteriores
Deberá poder salir del juego


## El juego 

El juego es de generales, los jugadores dan órdenes, las tropas las cumplen. 
Se jugará por turnos, se darán las órdenes indicando el camino en el mapa que debe seguir cada ejército
Al finalizar el turno el engine del juego calcula la posición siguiente de cada tropa
Cuando dos tropas enemigas se encuentren en el mapa el movimiento de esas dos tropas se detiene y se da una batalla
La batalla se calcula en base a la cantidad, clases y skills de cada ejército combinado con un número al azar para agregarle aleatoriedad.
Por cada batalla los ejércitos deben perder tropas y XP, una batalla puede terminar en empate o retirada, el ejército que se retira debe recibir mayor penalidad.

Por cada turno los pueblos proveen +1 de alimento
Por cada turno los fuertes producen tropas en base al alimento disponible (crear una fórmula)

En cada turno se deben calcular las condiciones de la victoria para validar en qué momento un jugador ganó al otro. 

## Tecnología

El juego sólo será 2D, no requiere un engine potente
Debe poder crearse una versión compilada y distribuible
Debe ser compatible con Windows y Linux

Necesito definir la tecnología a usar, tanto para la gráfica como para el código del juego 
Elección de engine (Godot?) ¿Algún otro? ¿Custom?
Elección de lenguaje de programación (C? C++? Python?)
Los assets del juego serán creados con imágenes, se deberá definir tamaño y forma que deban tener, lo ideal sería crear imágenes png que sirvan de placeholder para el arte final.






