"""Capa de red de WOM (multiplayer, v0.4.0).

Metodología: lockstep determinista con host autoritativo de respaldo (ver
`docs/multiplayer.md`). Ambos clientes corren la misma simulación del core con
las mismas órdenes y la misma seed; por la red solo viajan las órdenes de cada
jugador por turno (+ el estado inicial y las reglas al empezar).

Este paquete **no importa pygame** (igual que `core` y `ai`): depende solo del
core y de la stdlib. Lo verifica el test de humo.
"""
