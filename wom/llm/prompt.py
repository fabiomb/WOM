"""Construcción del system prompt para el jugador-LLM.

Se arma desde el `Game` real (clases de unidad y sus stats, modo de victoria,
tamaño máximo de ejército) para que las reglas que ve el modelo coincidan con la
config cargada, no con texto hardcodeado. El prompt explica el juego, el formato
de acciones JSON y exige responder SOLO con la lista de acciones.
"""

from __future__ import annotations

from wom.core.game import Game

_VICTORY_TEXT = {
    "total": "Aniquilación: ganás cuando el enemigo se queda sin ejércitos ni fuertes.",
    "flags": "Banderas: ganás al controlar la mayoría de los fuertes con bandera.",
    "time": "Tiempo: al llegar al límite de turnos gana quien tenga más territorio "
    "(fuertes + pueblos), y como desempate más tropas.",
}


def _classes_table(game: Game) -> str:
    lines = ["Clases de unidad (velocidad = tiles por turno; mayor ataque/defensa = mejor):"]
    for cls in sorted(game.classes.values(), key=lambda c: c.id):
        extra = " [ignora el bono de fuerte al atacar]" if cls.ignora_bonus_fort else ""
        lines.append(
            f"  - {cls.id} ({cls.nombre}): velocidad {cls.velocidad}, "
            f"ataque {cls.ataque}, defensa {cls.defensa}{extra}"
        )
    return "\n".join(lines)


def system_prompt(game: Game, player_id: int) -> str:
    """System prompt completo para `player_id` en esta partida."""
    victory = _VICTORY_TEXT.get(
        game.victory_mode.value, "Ganá según la condición de victoria de la partida."
    )
    max_army = game.config["max_army_size"]
    return f"""\
Sos un comandante en WOM, un juego de estrategia militar 2D por turnos sobre una
grilla. Controlás al jugador {player_id}. Jugás contra un rival que mueve en el
mismo turno que vos; las órdenes de ambos se resuelven juntas.

OBJETIVO: {victory}

CÓMO FUNCIONA EL TURNO:
- En cada turno das una lista de órdenes para tus ejércitos y fuertes.
- Movimiento: cada ejército avanza por un camino; su velocidad la marca su unidad
  más lenta. El agua es intransitable (los puentes la cruzan). El bosque y la
  montaña cuestan más moverse.
- Batalla: ocurre cuando un ejército intenta ENTRAR al tile de un enemigo. Cada
  bando pelea desde su tile; el defensor suma el bono de su terreno y, si está en
  un fuerte o pueblo, el bono de la fortificación. El perdedor retrocede un tile
  (salvo que esté sobre un fuerte: ahí no retrocede, hay que destruirlo).
- Fuertes: producen tropas que se acumulan en su "reserva". Esas tropas NO salen
  solas: tenés que crear un ejército en el fuerte (acción "create"). Un ejército
  propio parado en su fuerte se reabastece de la reserva. Capturar un fuerte
  enemigo destruye su reserva. Un ejército llega como máximo a {max_army} tropas.
- Pueblos: producen la comida que financia la producción. La comida baja también
  afecta la eficiencia en combate.
- Reorganización: podés fusionar dos ejércitos adyacentes propios, transferir
  tropas entre adyacentes, o dividir uno en dos (el nuevo aparece en un tile
  libre contiguo).

{_classes_table(game)}

FORMATO DE RESPUESTA — respondé ÚNICAMENTE con un arreglo JSON de acciones, sin
texto antes ni después, sin comentarios. Acciones válidas:
  {{"action": "move",     "army": <id>, "to": [x, y]}}        mover el ejército hacia el tile [x,y] (se calcula la ruta)
  {{"action": "create",   "fort": [x, y]}}                    crear un ejército en tu fuerte de [x,y]
  {{"action": "merge",    "source": <id>, "target": <id>}}    fusionar source dentro de target (deben ser adyacentes)
  {{"action": "split",    "source": <id>, "detach": {{"<clase>": <n>}}}}  separar tropas en un ejército nuevo
  {{"action": "transfer", "source": <id>, "target": <id>, "troops": {{"<clase>": <n>}}}}  pasar tropas a un adyacente
  {{"action": "wait"}}                                        pasar sin hacer nada

Reglas para que tus órdenes sean válidas:
- Usá solo ids de TUS ejércitos y coordenadas de TUS fuertes (los ves en la
  observación).
- Las coordenadas son [columna x, fila y]; x crece a la derecha, y hacia abajo.
- No muevas a tiles de agua; no pidas más tropas de las que tiene un ejército.
- Podés dar varias órdenes por turno (una por ejército como mucho para "move").
- Pensá tu estrategia internamente, pero la salida debe ser SOLO el JSON.
"""
