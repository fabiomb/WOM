"""Cámara del mapa: zoom discreto con la rueda y paneo por bordes.

Pura (sin pygame, como animation.py y tiling.py) para poder testearla
headless. Trabaja en píxeles del canvas lógico: el renderer le consulta
`tile_size` y `origin`; GameScreen la alimenta con la rueda del mouse
(`zoom`) y con la posición del mouse cada frame (`edge_pan`).

El nivel 0 es el encuadre clásico: el mapa entero centrado en el área.
Los niveles siguientes agrandan el tile manteniendo fijo el punto del mapa
bajo el cursor; el origen se clampa para no mostrar vacío más allá de los
bordes (y se centra en los ejes donde el mapa entra completo).
"""

from __future__ import annotations

ZOOM_FACTORS = (1.0, 1.35, 1.8, 2.4, 3.0)
MAX_TILE_SIZE = 128  # más allá los assets (64 px nativos) se pixelan demasiado
EDGE_MARGIN = 28  # franja del borde del área (px) que dispara el paneo
PAN_SPEED_TILES = 10.0  # velocidad de paneo, en tiles por segundo


class Camera:
    def __init__(
        self,
        area: tuple[int, int, int, int],
        map_tiles: tuple[int, int],
        base_tile: int,
    ):
        self.area = area  # (x, y, ancho, alto) del área de mapa en el canvas
        self.map_w, self.map_h = map_tiles
        sizes = [max(1, round(base_tile * f)) for f in ZOOM_FACTORS]
        self.sizes = [s for s in sizes if s <= MAX_TILE_SIZE] or sizes[:1]
        self.level = 0
        self._origin_x = 0.0
        self._origin_y = 0.0
        self._clamp()

    @property
    def tile_size(self) -> int:
        return self.sizes[self.level]

    @property
    def origin(self) -> tuple[int, int]:
        return (round(self._origin_x), round(self._origin_y))

    @property
    def zoomed(self) -> bool:
        return self.level > 0

    def zoom(self, direction: int, anchor: tuple[int, int]) -> bool:
        """Sube (direction > 0) o baja un nivel de zoom, manteniendo fijo el
        punto del mapa que está bajo `anchor` (posición del mouse en el
        canvas). Devuelve True si el nivel cambió."""
        new_level = min(max(self.level + (1 if direction > 0 else -1), 0),
                        len(self.sizes) - 1)
        if new_level == self.level:
            return False
        old = self.tile_size
        tile_x = (anchor[0] - self._origin_x) / old
        tile_y = (anchor[1] - self._origin_y) / old
        self.level = new_level
        self._origin_x = anchor[0] - tile_x * self.tile_size
        self._origin_y = anchor[1] - tile_y * self.tile_size
        self._clamp()
        return True

    def pan(self, dx: float, dy: float) -> None:
        """Desplaza la vista (la cámara) en píxeles; el mapa va al revés."""
        self._origin_x -= dx
        self._origin_y -= dy
        self._clamp()

    def edge_pan(self, mouse: tuple[int, int], dt: float) -> bool:
        """Paneo automático cuando el mouse toca los bordes del área de mapa.

        `dt` en segundos (tiempo del frame). Devuelve True si movió algo.
        """
        ax, ay, aw, ah = self.area
        mx, my = mouse
        if not (ax <= mx < ax + aw and ay <= my < ay + ah):
            return False
        dx = dy = 0
        if mx < ax + EDGE_MARGIN:
            dx = -1
        elif mx >= ax + aw - EDGE_MARGIN:
            dx = 1
        if my < ay + EDGE_MARGIN:
            dy = -1
        elif my >= ay + ah - EDGE_MARGIN:
            dy = 1
        if dx == 0 and dy == 0:
            return False
        before = (self._origin_x, self._origin_y)
        speed = PAN_SPEED_TILES * self.tile_size * dt
        self.pan(dx * speed, dy * speed)
        return (self._origin_x, self._origin_y) != before

    def _clamp(self) -> None:
        """Clampa el origen: centra los ejes donde el mapa entra en el área
        y limita a los bordes donde el mapa es más grande que el área."""
        ax, ay, aw, ah = self.area
        ts = self.tile_size
        map_px_w = self.map_w * ts
        map_px_h = self.map_h * ts
        if map_px_w <= aw:
            self._origin_x = ax + (aw - map_px_w) / 2
        else:
            self._origin_x = min(ax, max(ax + aw - map_px_w, self._origin_x))
        if map_px_h <= ah:
            self._origin_y = ay + (ah - map_px_h) / 2
        else:
            self._origin_y = min(ay, max(ay + ah - map_px_h, self._origin_y))
