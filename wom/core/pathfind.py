"""Búsqueda de caminos: Dijkstra sobre los costos de terreno.

Usado por la AI para elegir objetivos y trazar paths. El pathfinding ignora
a los ejércitos (no bloquean tiles): los encuentros con enemigos se detectan
durante la fase de movimiento del motor de turnos.
"""

from __future__ import annotations

import heapq

from wom.core.worldmap import Coord, WorldMap

INF = float("inf")


def dijkstra(
    world: WorldMap,
    start: Coord,
    terrain_costs: dict[str, int],
    extra_cost: dict[Coord, float] | None = None,
) -> tuple[dict[Coord, float], dict[Coord, Coord]]:
    """Distancias mínimas desde `start` a todo tile transitable.

    `extra_cost` agrega costo artificial por tile (p. ej. zonas de peligro
    cerca de ejércitos enemigos fuertes: la AI las rodea si hay desvío
    razonable). Devuelve (dist, prev): costo acumulado por tile y
    predecesor de cada tile para reconstruir caminos.
    """
    dist: dict[Coord, float] = {start: 0.0}
    prev: dict[Coord, Coord] = {}
    heap: list[tuple[float, Coord]] = [(0.0, start)]
    while heap:
        d, pos = heapq.heappop(heap)
        if d > dist.get(pos, INF):
            continue
        for neighbor in world.neighbors(pos):
            cost = terrain_costs[world.terrain_at(neighbor).value]
            if extra_cost is not None:
                cost += extra_cost.get(neighbor, 0.0)
            nd = d + cost
            if nd < dist.get(neighbor, INF):
                dist[neighbor] = nd
                prev[neighbor] = pos
                heapq.heappush(heap, (nd, neighbor))
    return dist, prev


def reconstruct_path(prev: dict[Coord, Coord], start: Coord, goal: Coord) -> list[Coord]:
    """Camino de start a goal (excluye start, incluye goal). [] si no hay."""
    if goal == start:
        return []
    if goal not in prev:
        return []
    path = [goal]
    current = goal
    while prev[current] != start:
        current = prev[current]
        path.append(current)
    return list(reversed(path))


def shortest_path(
    world: WorldMap, start: Coord, goal: Coord, terrain_costs: dict[str, int]
) -> list[Coord]:
    """Camino mínimo entre dos tiles. [] si no existe o start == goal."""
    _, prev = dijkstra(world, start, terrain_costs)
    return reconstruct_path(prev, start, goal)
