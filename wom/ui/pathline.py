"""Geometría de las rutas dibujadas: curvas suaves y punta de flecha.

Puro (sin pygame, como animation.py y tiling.py) para poder testearlo
headless. El renderer convierte el path de tiles a centros en pantalla y
este módulo lo transforma en un trazo "a mano alzada": esquinas redondeadas
por corte de Chaikin y un triángulo orientado como flecha final.
"""

from __future__ import annotations

from math import hypot

Point = tuple[float, float]

SMOOTH_ROUNDS = 3  # iteraciones de Chaikin (cada una duplica los segmentos)


def smooth_path(points: list[Point], rounds: int = SMOOTH_ROUNDS) -> list[Point]:
    """Suaviza una polilínea cortando esquinas (Chaikin), extremos fijos.

    Con menos de 3 puntos no hay esquinas que cortar: vuelve igual.
    """
    pts = [(float(x), float(y)) for x, y in points]
    for _ in range(rounds):
        if len(pts) < 3:
            break
        out = [pts[0]]
        for (ax, ay), (bx, by) in zip(pts, pts[1:]):
            out.append((ax * 0.75 + bx * 0.25, ay * 0.75 + by * 0.25))
            out.append((ax * 0.25 + bx * 0.75, ay * 0.25 + by * 0.75))
        out.append(pts[-1])
        pts = out
    return pts


def arrow_head(points: list[Point], size: float) -> list[Point]:
    """Triángulo de flecha en el último punto, orientado según el trazo.

    Devuelve [punta, ala izquierda, ala derecha], o [] si la polilínea no
    tiene dirección (un solo punto o todos coincidentes).
    """
    if len(points) < 2:
        return []
    tip_x, tip_y = points[-1]
    for prev_x, prev_y in reversed(points[:-1]):
        dx, dy = tip_x - prev_x, tip_y - prev_y
        length = hypot(dx, dy)
        if length > 1e-6:
            break
    else:
        return []
    ux, uy = dx / length, dy / length
    base_x, base_y = tip_x - ux * size, tip_y - uy * size
    half = size * 0.55
    return [
        (tip_x, tip_y),
        (base_x - uy * half, base_y + ux * half),
        (base_x + uy * half, base_y - ux * half),
    ]


def trim_tail(points: list[Point], distance: float) -> list[Point]:
    """Recorta `distance` píxeles del final de la polilínea (para que el
    trazo no asome por debajo de la flecha). Si la polilínea es más corta
    que el recorte, devuelve solo el primer punto."""
    if len(points) < 2 or distance <= 0:
        return points
    remaining = distance
    pts = list(points)
    while len(pts) >= 2:
        (ax, ay), (bx, by) = pts[-2], pts[-1]
        seg = hypot(bx - ax, by - ay)
        if seg > remaining:
            t = (seg - remaining) / seg
            pts[-1] = (ax + (bx - ax) * t, ay + (by - ay) * t)
            return pts
        remaining -= seg
        pts.pop()
    return pts[:1]
