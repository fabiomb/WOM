"""Formaciones de combate para el zoom de batalla (wom/core/tactical.py).

Módulo **puro** (sin pygame ni RNG): reordena las fichas de un bando dentro de
su zona de despliegue según una formación elegida. Es determinista, así que no
toca el invariante de aleatoriedad del juego (de hecho, reemplaza el sembrado
al azar de las fichas por una disposición ordenada).

Cuatro formaciones (el jugador las elige con 1/2/3/4 durante la preparación;
la IA elige la que le conviene según su composición):

- **línea** (`linea`): todas en una línea ancha al frente. Es la de defecto y
  la que se usa cuando el ejército es chico (no hay tropas para más).
- **clásica** (`clasica`): arqueros detrás, infantería al frente, caballería en
  los flancos.
- **compacta** (`compacta`): un bloque casi cuadrado y apretado.
- **en V** (`v`): la infantería forma una V con la punta hacia el enemigo;
  caballería y arqueros en los flancos traseros (las bocas de la V).

Coordenadas internas: cada ficha se ubica con `(f, l)` donde `f` es la
*profundidad* (0 = retaguardia, 1 = frente, hacia el enemigo) y `l` el
*lateral* (0..1 a lo ancho del campo). `arrange` mapea ese marco a la zona real
según `facing` (+1 si el bando avanza hacia +x —el atacante—, -1 si hacia -x).
"""

from __future__ import annotations

import math

# Claves de formación (las teclas 1..4 mapean a este orden).
LINE = "linea"
CLASSIC = "clasica"
COMPACT = "compacta"
VEE = "v"
FORMATIONS = (LINE, CLASSIC, COMPACT, VEE)
FORMATION_NAMES = {
    LINE: "Línea",
    CLASSIC: "Clásica",
    COMPACT: "Compacta",
    VEE: "En V",
}

# Heurísticas de agrupación (no son balance: clasifican roles para formar).
CAVALRY_MIN_SPEED = 5   # velocidad ≥ esto y no-arquero ⇒ caballería (flancos)
SMALL_ARMY = 12         # tropas por debajo de esto ⇒ la línea simple alcanza


# --- API pública ---------------------------------------------------------
def arrange(units, zone, *, facing: int, formation: str, classes) -> None:
    """Reubica (muta `x`/`y`) las fichas de un bando dentro de `zone`.

    `zone` es `(x0, x1, y0, y1)` en celdas del campo; `facing` +1 si el bando
    mira hacia +x (atacante) o -1 si hacia -x (defensor). `formation` es una de
    las claves de `FORMATIONS`; cualquier otra cae en línea.
    """
    units = list(units)
    if not units:
        return
    x0, x1, y0, y1 = zone
    for u, (f, l) in _layout(units, formation, classes):
        depth = f if facing > 0 else 1.0 - f  # el frente mira al enemigo
        u.x = x0 + _clamp01(depth) * (x1 - x0)
        u.y = y0 + _clamp01(l) * (y1 - y0)


def recommend(units, classes) -> str:
    """Formación que le conviene a un bando según su composición.

    Ejércitos chicos → línea (por defecto). Con armas combinadas (arqueros +
    algo) → clásica; caballería + infantería → en V; si no, bloque compacto.
    """
    total = sum(u.troops0 for u in units)
    if total < SMALL_ARMY:
        return LINE
    inf, cav, ranged = _split_roles(units, classes)
    if ranged and (inf or cav):
        return CLASSIC
    if cav and inf:
        return VEE
    return COMPACT


# --- disposición por formación -------------------------------------------
def _layout(units, formation, classes):
    if formation == CLASSIC:
        return _classic(units, classes)
    if formation == COMPACT:
        return _compact(units)
    if formation == VEE:
        return _vee(units, classes)
    return _line(units)


def _line(units):
    """Línea ancha y poco profunda al frente (1 fila si entran, más si no)."""
    ranks = max(1, math.ceil(len(units) / 16))
    return _block(units, 0.62, 0.92, 0.04, 0.96, ranks)


def _compact(units):
    """Bloque casi cuadrado y apretado, centrado."""
    ranks = max(1, round(math.sqrt(len(units))))
    return _block(units, 0.45, 0.85, 0.28, 0.72, ranks)


def _classic(units, classes):
    """Arqueros atrás, infantería al frente, caballería en los flancos."""
    inf, cav, ranged = _split_roles(units, classes)
    out = []
    out += _block(inf, 0.70, 0.95, 0.18, 0.82, max(1, math.ceil(len(inf) / 12)))
    out += _block(ranged, 0.06, 0.30, 0.25, 0.75, max(1, math.ceil(len(ranged) / 12)))
    out += _flanks(cav, 0.45, 0.85)
    return out


def _vee(units, classes):
    """Infantería en V (punta al enemigo); caballería y arqueros en los flancos
    traseros (las bocas de la V)."""
    inf, cav, ranged = _split_roles(units, classes)
    out = _v_shape(inf)
    flank = cav + ranged
    half = math.ceil(len(flank) / 2)
    out += _column(flank[:half], 0.10, 0.40, 0.05)
    out += _column(flank[half:], 0.10, 0.40, 0.95)
    return out


# --- primitivas de colocación --------------------------------------------
def _block(units, f_lo, f_hi, l_lo, l_hi, ranks):
    """Coloca las fichas en `ranks` filas (la fila 0 = la del frente, en
    `f_hi`) repartidas a lo ancho de `[l_lo, l_hi]`."""
    n = len(units)
    if n == 0:
        return []
    ranks = max(1, min(ranks, n))
    files = math.ceil(n / ranks)
    out = []
    for i, u in enumerate(units):
        r, c = divmod(i, files)
        count = min(files, n - r * files)  # la última fila puede ser más corta
        f = _lerp(f_hi, f_lo, r, ranks)
        l = _spread(c, count, l_lo, l_hi)
        out.append((u, (f, l)))
    return out


def _column(units, f_lo, f_hi, l):
    """Una columna vertical (un solo `l`) escalonada en profundidad."""
    n = len(units)
    return [(u, (_lerp(f_hi, f_lo, i, n), l)) for i, u in enumerate(units)]


def _flanks(units, f_lo, f_hi):
    """Reparte las fichas en dos columnas, en el flanco izquierdo y derecho."""
    half = math.ceil(len(units) / 2)
    return _column(units[:half], f_lo, f_hi, 0.06) + _column(
        units[half:], f_lo, f_hi, 0.94
    )


def _v_shape(units):
    """V con la punta al frente-centro y los brazos abriéndose hacia atrás."""
    half = math.ceil(len(units) / 2)
    out = []
    for sign, arm in ((-1, units[:half]), (1, units[half:])):
        for i, u in enumerate(arm):
            t = i / max(1, len(arm))  # 0 en la punta, → 1 en la boca
            out.append((u, (0.95 - 0.5 * t, 0.5 + sign * 0.42 * t)))
    return out


# --- roles ----------------------------------------------------------------
def _split_roles(units, classes):
    """Separa las fichas en (infantería, caballería, arqueros)."""
    inf, cav, ranged = [], [], []
    for u in units:
        role = _role(u.class_id, classes)
        (ranged if role == "ranged" else cav if role == "cavalry" else inf).append(u)
    return inf, cav, ranged


def _role(class_id, classes) -> str:
    cls = classes[class_id]
    if getattr(cls, "ignora_bonus_fort", False):
        return "ranged"          # arquero: dispara desde atrás
    if cls.velocidad >= CAVALRY_MIN_SPEED:
        return "cavalry"         # caballería: flancos
    return "infantry"


# --- utilidades numéricas -------------------------------------------------
def _lerp(a, b, idx, count):
    return a if count <= 1 else a + (b - a) * idx / (count - 1)


def _spread(idx, count, lo, hi):
    return (lo + hi) / 2 if count <= 1 else lo + (hi - lo) * idx / (count - 1)


def _clamp01(v):
    return 0.0 if v < 0.0 else 1.0 if v > 1.0 else v
