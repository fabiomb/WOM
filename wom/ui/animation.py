"""Animación del fin de turno: marcha, choque de batallas y retiradas.

El core registra en `Game.last_moves` los tiles que pisó cada ejército, en
`Game.last_clashes` los pares (atacante, defensor) de cada batalla y en
`Game.last_retreats` quiénes se retiraron tras pelear; con eso se arma una
línea de tiempo en tres fases:

1. Marcha    — cada ejército recorre sus waypoints (sin el paso de
               retirada) a velocidad constante, todos en simultáneo.
2. Choque    — si hubo batallas, ambos bandos se embisten varias veces
               durante CLASH_SECONDS con un destello en el punto de
               contacto. El resultado recién se "revela" al terminar esta
               fase: hasta entonces los involucrados muestran sus tropas
               previas a la batalla, y los que murieron desaparecen recién
               ahí (queda su cruz, que la UI también retiene hasta el
               final del choque).
3. Retirada  — los que se retiraron animan recién ahora su último paso.

También vive acá el resaltado del ejército inicial (anillos que se
expanden y esfuman alrededor de su tile en los primeros segundos de una
partida nueva), porque es la misma matemática de interpolación.

Módulo sin pygame a propósito: todo es matemática pura y se testea sin
display (el tiempo entra como parámetro).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace

from wom.core.worldmap import Coord

TILES_PER_SECOND = 6.0

# Fase de choque: las tropas se embisten CLASH_PULSES veces en CLASH_SECONDS,
# avanzando hasta CLASH_AMPLITUDE tiles hacia el rival en cada embestida.
CLASH_SECONDS = 1.8
CLASH_PULSES = 3
CLASH_AMPLITUDE = 0.35

# Resaltado del ejército inicial: anillos desfasados que nacen chicos y
# opacos y mueren grandes y transparentes, durante los primeros segundos.
SPAWN_HIGHLIGHT_SECONDS = 6.0
SPAWN_RING_PERIOD = 1.2      # segundos que tarda un anillo en expandirse
SPAWN_RING_COUNT = 3         # anillos simultáneos, desfasados entre sí
SPAWN_RING_MIN_RADIUS = 0.5  # radios en tiles
SPAWN_RING_MAX_RADIUS = 2.2

# Posición fraccionaria en coordenadas de tile (para interpolar entre tiles).
FloatCoord = tuple[float, float]


# --- animación de sprites por frames (soldado y futuras clases) -----------
# Elegir el frame es matemática pura (se testea sin pygame); el dibujo vive en
# renderer.py / battle_screen.py. Los tiempos por defecto:
WALK_FPS = 6.0          # cuadros por segundo del ciclo de caminata (loop)
ATTACK_SECONDS = 0.42   # duración de la secuencia de ataque (one-shot)
DEATH_SECONDS = 1.1     # cuánto se muestra la pose de muerte antes de desaparecer


def loop_frame(elapsed: float, n_frames: int, *, fps: float = WALK_FPS,
               phase: float = 0.0) -> int:
    """Índice de un ciclo que loopea (caminata): avanza a `fps` y da la vuelta.
    `phase` desfasa el ciclo (para que no marchen todos igual)."""
    if n_frames <= 1:
        return 0
    return int((elapsed + phase) * fps) % n_frames


def oneshot_frame(t: float, n_frames: int, duration: float) -> int:
    """Índice de una secuencia que se reproduce una vez en `duration` y mantiene
    el último cuadro (ataque). `t` es el tiempo desde que arrancó."""
    if n_frames <= 1 or duration <= 0:
        return 0
    return max(0, min(n_frames - 1, int((t / duration) * n_frames)))


def pick_frame(seed: int, n_frames: int) -> int:
    """Elige un cuadro de forma determinista por `seed` (p. ej. la muerte, que
    usa una pose al azar por unidad). Determinista a propósito: nunca usa el RNG
    del juego, así la simulación sigue siendo reproducible."""
    if n_frames <= 1:
        return 0
    return seed % n_frames


def spawn_highlight_rings(elapsed_seconds: float) -> list[tuple[float, float]]:
    """Anillos (radio en tiles, opacidad 0..1) del resaltado inicial.

    Señala dónde está el ejército inicial del jugador: cada anillo se
    expande y esfuma en SPAWN_RING_PERIOD y el conjunto se apaga suave en
    el último segundo. Vacío fuera de SPAWN_HIGHLIGHT_SECONDS.
    """
    if not 0.0 <= elapsed_seconds < SPAWN_HIGHLIGHT_SECONDS:
        return []
    fade = min(1.0, SPAWN_HIGHLIGHT_SECONDS - elapsed_seconds)
    rings = []
    for i in range(SPAWN_RING_COUNT):
        t = (elapsed_seconds / SPAWN_RING_PERIOD + i / SPAWN_RING_COUNT) % 1.0
        radius = SPAWN_RING_MIN_RADIUS + t * (
            SPAWN_RING_MAX_RADIUS - SPAWN_RING_MIN_RADIUS
        )
        rings.append((radius, (1.0 - t) * fade))
    return rings


@dataclass(frozen=True)
class ArmyMotion:
    """Lo necesario para dibujar un ejército en marcha, congelado al animar.

    `alive` distingue a los que mueren en el turno: animan su recorrido
    completo y desaparecen al terminar el choque (queda su cruz).
    """

    army_id: int
    owner: int
    class_id: str  # clase dominante (elige el sprite)
    troops: int
    waypoints: list[Coord]  # tiles pisados este turno; [pos] si no se movió
    alive: bool = True

    def position_at(self, elapsed_seconds: float) -> FloatCoord:
        """Posición interpolada tras `elapsed_seconds` de animación."""
        progress = elapsed_seconds * TILES_PER_SECOND  # en tiles recorridos
        last = len(self.waypoints) - 1
        if progress >= last:
            return tuple(map(float, self.waypoints[-1]))
        i = int(progress)
        t = progress - i
        (x0, y0), (x1, y1) = self.waypoints[i], self.waypoints[i + 1]
        return (x0 + (x1 - x0) * t, y0 + (y1 - y0) * t)

    @property
    def duration(self) -> float:
        """Segundos que tarda en recorrer sus waypoints."""
        return (len(self.waypoints) - 1) / TILES_PER_SECOND


class TurnAnimation:
    """Anima un turno en fases (marcha, choque, retirada); `finished` al final.

    Sin batallas se reduce a la fase de marcha, como siempre. Con batallas,
    a cada ejército involucrado se le recorta el paso de retirada de sus
    waypoints: espera el choque en su posición de combate y recién después
    anima la retirada.
    """

    def __init__(
        self,
        motions: list[ArmyMotion],
        clashes: list[tuple[int, int]] = (),
        retreated_ids: frozenset[int] = frozenset(),
        troops_before: dict[int, int] | None = None,
    ):
        self.motions: list[ArmyMotion] = []
        self._retreats: dict[int, ArmyMotion] = {}
        for motion in motions:
            if motion.army_id in retreated_ids and len(motion.waypoints) >= 2:
                self._retreats[motion.army_id] = replace(
                    motion, waypoints=motion.waypoints[-2:]
                )
                motion = replace(motion, waypoints=motion.waypoints[:-1])
            self.motions.append(motion)

        # Por choque: dirección de embestida de cada bando y punto del
        # destello (medio camino entre ambos, en su posición de combate).
        ends = {m.army_id: m.waypoints[-1] for m in self.motions}
        self._clash_dirs: dict[int, FloatCoord] = {}
        self.clash_points: list[FloatCoord] = []
        for attacker_id, defender_id in clashes:
            a, d = ends.get(attacker_id), ends.get(defender_id)
            if a is None or d is None:
                continue
            dx, dy = d[0] - a[0], d[1] - a[1]
            norm = math.hypot(dx, dy) or 1.0
            self._clash_dirs[attacker_id] = (dx / norm, dy / norm)
            self._clash_dirs[defender_id] = (-dx / norm, -dy / norm)
            self.clash_points.append(((a[0] + d[0]) / 2, (a[1] + d[1]) / 2))

        # Hasta que el choque revela el resultado, los que pelearon muestran
        # sus tropas previas a la batalla (`troops_before`: army_id → conteo
        # pre-turno); el conteo final aparecería antes de definirse nada.
        troops_before = troops_before or {}
        self._pre_clash = {
            m.army_id: replace(m, troops=troops_before[m.army_id])
            for m in self.motions
            if m.army_id in self._clash_dirs and m.army_id in troops_before
        }

        self.move_duration = max((m.duration for m in self.motions), default=0.0)
        self.clash_duration = CLASH_SECONDS if self.clash_points else 0.0
        retreat_duration = max(
            (m.duration for m in self._retreats.values()), default=0.0
        )
        self.duration = self.move_duration + self.clash_duration + retreat_duration
        self._skipped = False

    def finished(self, elapsed_seconds: float) -> bool:
        return self._skipped or elapsed_seconds >= self.duration

    def skip(self) -> None:
        """Corta la animación (Enter/Espacio/click): el estado final ya existe."""
        self._skipped = True

    def result_revealed(self, elapsed_seconds: float) -> bool:
        """True cuando el choque ya terminó y el resultado puede mostrarse
        (conteos finales de tropas, cruces nuevas)."""
        return self._skipped or elapsed_seconds >= (
            self.move_duration + self.clash_duration
        )

    def positions(self, elapsed_seconds: float) -> list[tuple[ArmyMotion, FloatCoord]]:
        """Posición de cada ejército a dibujar en este instante.

        Los muertos del turno se omiten una vez terminado el choque (queda
        su cruz); los que se retiran interpolan su último paso recién en la
        fase de retirada. Antes de revelarse el resultado, los involucrados
        en batallas muestran su conteo de tropas previo.
        """
        clash_end = self.move_duration + self.clash_duration
        revealed = self.result_revealed(elapsed_seconds)
        result = []
        for motion in self.motions:
            retreat = self._retreats.get(motion.army_id)
            if retreat is not None and revealed:
                result.append(
                    (motion, retreat.position_at(elapsed_seconds - clash_end))
                )
                continue
            if not motion.alive and revealed:
                continue  # murió este turno: desaparece tras el choque
            if not revealed:
                motion = self._pre_clash.get(motion.army_id, motion)
            result.append((motion, self._with_clash_offset(motion, elapsed_seconds)))
        return result

    def _with_clash_offset(
        self, motion: ArmyMotion, elapsed_seconds: float
    ) -> FloatCoord:
        pos = motion.position_at(elapsed_seconds)
        direction = self._clash_dirs.get(motion.army_id)
        bump = self._clash_bump(elapsed_seconds)
        if direction is None or bump <= 0.0:
            return pos
        return (pos[0] + direction[0] * bump, pos[1] + direction[1] * bump)

    def _clash_bump(self, elapsed_seconds: float) -> float:
        """Avance (en tiles) de la embestida en este instante; 0 fuera de fase."""
        if not self.clash_duration or self._skipped:
            return 0.0
        t = (elapsed_seconds - self.move_duration) / self.clash_duration
        if not 0.0 <= t < 1.0:
            return 0.0
        return CLASH_AMPLITUDE * math.sin(math.pi * ((t * CLASH_PULSES) % 1.0))

    def clash_effects(self, elapsed_seconds: float) -> list[tuple[FloatCoord, float]]:
        """(posición, intensidad 0..1) del destello de cada batalla en choque."""
        bump = self._clash_bump(elapsed_seconds)
        if bump <= 0.0:
            return []
        intensity = bump / CLASH_AMPLITUDE
        return [(point, intensity) for point in self.clash_points]


def build_turn_animation(game, pre_turn_armies: list[dict]) -> TurnAnimation | None:
    """Arma la animación tras `run_turn`, o None si no hubo nada que mostrar.

    `pre_turn_armies` es el snapshot tomado antes del turno (dicts de
    Army.to_dict()): conserva sprite y posición de los ejércitos que
    murieron durante el turno. Los creados este turno (no están en el
    snapshot) aparecen quietos en su fuerte. Una batalla sin movimiento
    (ambos ya adyacentes) también anima: el choque es el feedback.
    """
    motions: list[ArmyMotion] = []
    seen: set[int] = set()
    # Ejércitos que pelearon (atacante o defensor) y posiciones donde quedó una
    # cruz este turno: sirven para distinguir un ejército ABSORBIDO por una
    # fusión —desaparecido antes del movimiento, sin batalla ni cruz— de uno
    # que murió. El absorbido no debe animarse quieto durante toda la marcha
    # (haría parecer que la fusión ocurre DESPUÉS del avance): se omite, así la
    # fusión se ve resuelta antes de mover.
    clash_ids = {aid for clash in game.last_clashes for aid in clash}
    recent_crosses = {(c[0], c[1]) for c in game.crosses if c[2] == game.turn - 1}
    for data in pre_turn_armies:
        army_id = data["id"]
        seen.add(army_id)
        current = game.army_by_id(army_id)
        if (
            current is None
            and army_id not in clash_ids
            and army_id not in game.last_moves
            and tuple(data["position"]) not in recent_crosses
        ):
            continue  # absorbido por una fusión: ya no está al empezar la marcha
        waypoints = game.last_moves.get(army_id) or [tuple(data["position"])]
        # Conteo final: evita el "salto" al terminar la animación. Para los
        # que pelearon, TurnAnimation muestra el previo hasta el choque.
        troops = current.total_troops if current else sum(data["composition"].values())
        composition = current.composition if current else data["composition"]
        motions.append(
            ArmyMotion(
                army_id=army_id,
                owner=data["owner"],
                class_id=_dominant(composition),
                troops=troops,
                waypoints=[tuple(p) for p in waypoints],
                alive=current is not None,
            )
        )
    for army in game.armies:  # creados durante el turno: estáticos
        if army.id not in seen:
            motions.append(
                ArmyMotion(
                    army_id=army.id,
                    owner=army.owner,
                    class_id=_dominant(army.composition),
                    troops=army.total_troops,
                    waypoints=[army.position],
                )
            )
    if all(len(m.waypoints) <= 1 for m in motions) and not game.last_clashes:
        return None  # turno sin movimiento ni batallas: nada que animar
    return TurnAnimation(
        motions,
        list(game.last_clashes),
        frozenset(game.last_retreats),
        troops_before={
            data["id"]: sum(data["composition"].values()) for data in pre_turn_armies
        },
    )


def _dominant(composition: dict[str, int]) -> str:
    return max(composition, key=lambda c: composition[c])
