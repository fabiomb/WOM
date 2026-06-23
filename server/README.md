# Servidor dedicado de WOM

Servidor online stand-alone para alojar partidas de WOM (lobby con chat + varias
partidas simultáneas; el servidor es la **autoridad** que resuelve los turnos y
las batallas). Diseño completo en [`../docs/server.md`](../docs/server.md).

Es **stdlib-only**: no necesita pygame ni paquetes de terceros, solo Python
3.11+ (el proyecto usa 3.13) y las capas puras de `wom/`.

## Uso (desarrollo)

Desde la raíz del repo:

```bash
# validar config + entorno (no abre el puerto)
python -m server --config server/server.toml --check

# arrancar el servidor (el loop de atención llega en una fase posterior)
python -m server --config server/server.toml
```

Sin `--config` se usan los valores por defecto (ver `server.toml`).

## Despliegue

El paquete mínimo para instalar el servidor en un host (solo las capas puras de
`wom/` + `data/config` + `data/scenarios` + este `server/`) se arma con
`tools/pack_server.py`. El manual de instalación, servicio systemd y apertura de
puerto en firewalls de Linux se documenta en `docs/server.md` §11 (y se ampliará
en `docs/server_deploy.md` en la fase S9).

## Estado

Fases S0 (esqueleto) y S1 (protocolo de lobby) implementadas. El loop de
atención, el lobby (`wom/net/lobby.py`), las partidas server-side
(`wom/net/match.py`), el anti-DDOS y la UI cliente llegan en fases posteriores
(ver `docs/server.md` §12).
