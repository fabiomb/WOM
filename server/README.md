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
`tools/pack_server.py`. El **manual completo** (instalación, servicio systemd,
apertura de puerto y rate-limiting con UFW/nftables/iptables/firewalld, fail2ban,
túnel cifrado y verificación) está en
[`../docs/server_deploy.md`](../docs/server_deploy.md). La unidad systemd de
ejemplo es [`wom-server.service`](wom-server.service).

## Estado

Servidor online **v0.7.0 completo**: lobby con chat + varias partidas
simultáneas (`wom/net/lobby.py`), partidas autoritativas player-less
(`wom/net/match.py`), anti-DDOS, orquestador (`server/game_server.py`), cliente
(`wom/net/server_session.py`) y navegador de servidores en la UI
(`wom/ui/server_browser_screen.py`). Diseño en `docs/server.md`, deploy en
`docs/server_deploy.md`.
