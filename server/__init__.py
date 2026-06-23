"""Servidor online dedicado de WOM (stand-alone).

Proceso que se instala en un host abierto a Internet para alojar partidas de
WOM: mantiene un **lobby** de jugadores con chat, aloja **varias partidas a la
vez** y actúa como **autoridad** de cada una (corre `Game.run_turn`, resuelve
las batallas y arbitra el lockstep), igual que hoy hace el host en LAN, pero
**sin ser jugador**. Diseño rector en `docs/server.md`.

Es **stdlib-only**: reusa solo las capas puras de `wom/` (core, net, mapgen
—dentro de core—, persistence, ai), que no dependen de pygame ni de paquetes de
terceros. No importa pygame (lo verifica el test de humo).

La lógica con reglas vive en `wom/net/` (`lobby.py`, `match.py`, testeable
headless); este paquete es la cáscara de I/O: loop de sockets, config y logging.
"""
