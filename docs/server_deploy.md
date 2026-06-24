# WOM — Manual de despliegue del servidor dedicado

Guía paso a paso para instalar el **servidor online de WOM** (v0.7.0) en un host
Linux abierto a Internet: armar el paquete, configurarlo, dejarlo corriendo como
servicio (systemd), abrir el puerto en el firewall con rate-limiting, y verificar
que se puede entrar desde afuera. El diseño está en [`server.md`](server.md).

El servidor es **stdlib-only** (Python 3.11+, sin pygame ni paquetes de
terceros): el despliegue es copiar una carpeta y correr `python -m server`.

> Convención: el puerto por defecto es **50000/TCP**. Cambialo en `server.toml`
> y en las reglas de firewall si usás otro.

---

## 1. Requisitos

- Un host Linux (VPS o máquina propia) con **Python 3.10 o superior**.
  - Con **3.11+** no hace falta nada más (el lector de TOML viene en la stdlib).
  - Con **3.10** (p. ej. Ubuntu 22.04 LTS) instalá el paquete `tomli`:
    `pip3 install tomli` o `sudo apt install python3-tomli`.
- Acceso `sudo` para crear el servicio y abrir el puerto.
- El puerto del servidor accesible desde Internet (IP pública directa o
  port-forward en el router — ver §7).

No hace falta instalar el juego completo ni dependencias gráficas.

---

## 2. Armar el paquete del servidor

En tu máquina de desarrollo (con el repo de WOM), generá el paquete mínimo:

```bash
python tools/pack_server.py --out dist/wom-server
```

Esto copia **solo lo necesario** (las capas puras `wom/core`, `wom/net`,
`wom/persistence`, `wom/ai`, los datos `data/config` + `data/scenarios`, y la
carpeta `server/`) a `dist/wom-server/`. No incluye la UI ni pygame.

Copiá esa carpeta al host (por ejemplo con `scp` o `rsync`):

```bash
rsync -av dist/wom-server/ usuario@TU_HOST:/opt/wom-server/
```

---

## 3. Configurar (`server.toml`)

En el host, editá `/opt/wom-server/server.toml`:

```toml
[server]
host = "0.0.0.0"          # escuchar en todas las interfaces
port = 50000
name = "Mi servidor WOM"

[auth]
require_password = false  # true para pedir contraseña al entrar
password = "cambiame"     # se ignora si require_password = false

[limits]
max_connections = 200
max_connections_per_ip = 8
max_matches = 20
handshake_timeout = 10
msg_rate = 30
msg_burst = 60

[maps]
scenarios_dir = "data/scenarios"
allow_random = true

[logging]
level = "info"
file = "wom-server.log"
```

Los `[limits]` son las mitigaciones anti-DDOS de la aplicación (topes de
conexión por IP, rate-limit de mensajes, timeout de handshake). Lo que es del
sistema operativo (floods SYN, etc.) se cubre con el firewall en §6 y §8.

---

## 4. Probar la instalación

Validá el entorno y la config **sin abrir el puerto**:

```bash
cd /opt/wom-server
python3 -m server --config server.toml --check
```

Debe imprimir el resumen y `OK: entorno y configuración válidos.`

Después, probá levantarlo en primer plano (Ctrl+C para cortar):

```bash
python3 -m server --config server.toml
```

---

## 5. Servicio systemd (que arranque solo y se reinicie)

Crear un usuario sin privilegios para correr el servidor:

```bash
sudo useradd --system --no-create-home --shell /usr/sbin/nologin womserver
sudo chown -R womserver:womserver /opt/wom-server
```

Copiar la unidad de ejemplo ([`server/wom-server.service`](../server/wom-server.service))
a systemd y ajustar rutas si hace falta:

```ini
[Unit]
Description=Servidor online de WOM
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=womserver
Group=womserver
WorkingDirectory=/opt/wom-server
ExecStart=/usr/bin/python3 -m server --config server.toml
Restart=on-failure
RestartSec=3
# Endurecimiento básico
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/opt/wom-server

[Install]
WantedBy=multi-user.target
```

```bash
sudo cp /opt/wom-server/server/wom-server.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now wom-server
sudo systemctl status wom-server          # ver estado
journalctl -u wom-server -f               # ver logs en vivo
```

`Restart=on-failure` reinicia el servidor si se cae (pero NO si terminás vos con
`systemctl stop`).

---

## 6. Abrir el puerto en el firewall

Elegí según el firewall de tu distro. Todos abren **50000/TCP**.

### UFW (Ubuntu/Debian)

```bash
sudo ufw allow 50000/tcp
# Opcional: limitar nuevas conexiones por IP (mitiga floods de conexión)
sudo ufw limit 50000/tcp
```

### firewalld (Fedora/RHEL/CentOS)

```bash
sudo firewall-cmd --add-port=50000/tcp --permanent
sudo firewall-cmd --reload
```

### nftables (con rate-limit por IP)

Agregá a tu tabla `inet filter`, cadena `input`:

```nft
# Acepta conexiones ya establecidas
ct state established,related accept
# Nuevas conexiones a WOM: máximo 10 por minuto por IP de origen
tcp dport 50000 ct state new \
    meter wom_conn { ip saddr limit rate 10/minute burst 20 packets } accept
# (sin meter, simplemente: tcp dport 50000 accept)
```

```bash
sudo nft -f /etc/nftables.conf && sudo systemctl restart nftables
```

### iptables (con hashlimit por IP)

```bash
sudo iptables -A INPUT -p tcp --dport 50000 -m conntrack --ctstate ESTABLISHED -j ACCEPT
sudo iptables -A INPUT -p tcp --dport 50000 -m conntrack --ctstate NEW \
    -m hashlimit --hashlimit-name wom --hashlimit-mode srcip \
    --hashlimit-above 10/min --hashlimit-burst 20 -j DROP
sudo iptables -A INPUT -p tcp --dport 50000 -j ACCEPT
# Persistir (Debian/Ubuntu): sudo netfilter-persistent save
```

El rate-limit del firewall acota la **tasa de conexiones nuevas** por IP; el
rate-limit de **mensajes** ya lo hace la aplicación (`[limits]` en §3).

---

## 7. Reenvío de puerto (NAT doméstico)

Si el host está detrás de un router casero:

1. Asignale una IP local fija al host (DHCP reservation).
2. En el router, reenviá **TCP 50000 externo → IP_local:50000**.
3. Averiguá tu IP pública: `curl ifconfig.me`. Esa es la que comparten los
   jugadores (junto al puerto). Si es dinámica, considerá un DNS dinámico.

---

## 8. (Opcional) fail2ban contra abuso

El servidor loguea los rechazos. Podés banear IPs que disparen muchos
`RATE_LIMITED` o handshakes fallidos con un filtro de fail2ban sobre
`wom-server.log` (o el journal). Filtro de ejemplo
(`/etc/fail2ban/filter.d/wom.conf`):

```ini
[Definition]
failregex = .*(RATE_LIMITED|conexión rechazada).*<HOST>.*
ignoreregex =
```

> Nota: el log actual no incluye la IP en cada línea de rechazo. Si querés
> usar fail2ban en serio, conviene primero sumar la IP a esos mensajes de log
> (mejora pendiente); mientras tanto, el rate-limit del firewall (§6) es la
> defensa principal a nivel SO.

---

## 9. (Opcional) Túnel cifrado

El protocolo viaja en **JSON en claro** (no maneja datos sensibles más allá del
nick). Si querés confidencialidad en Internet público, exponé el puerto detrás
de un túnel cifrado en vez de directo:

- **WireGuard**: los jugadores entran a una VPN y conectan a la IP interna del
  servidor.
- **Túnel SSH**: `ssh -L 50000:localhost:50000 usuario@host` y conectan a
  `127.0.0.1:50000` (solo para grupos chicos/confianza).

Con un túnel, podés dejar el puerto del servidor cerrado al público y abrir solo
el del túnel.

---

## 10. Verificación y problemas comunes

Desde **otra red** (no la del host), un jugador abre WOM →
**Multijugador → Jugar por Internet**, agrega el servidor (IP pública + puerto)
y conecta. Debería ver el lobby.

Si no conecta, revisá en orden:

| Síntoma | Causa probable | Solución |
|---|---|---|
| "No se pudo conectar" | Puerto cerrado o IP equivocada | Verificá firewall (§6) y port-forward (§7); probá `nc -vz IP 50000` |
| Conecta y rebota al instante | Versión de WOM o config de balance distinta | Cliente y servidor deben tener la misma versión y `data/config` (lo valida el handshake) |
| "contraseña incorrecta" | `require_password=true` | Compartí la contraseña o desactivala |
| "el servidor está lleno" | Tope `max_connections` | Subilo en `server.toml` y reiniciá |
| "demasiadas conexiones desde tu IP" | Tope `max_connections_per_ip` | Normal si varios entran tras un mismo NAT; subí el tope |
| Se cae solo y vuelve | `Restart=on-failure` reiniciando ante un bug | Mirá `journalctl -u wom-server` |

---

## 11. Operar el servidor

```bash
sudo systemctl restart wom-server   # reiniciar (tras editar server.toml)
sudo systemctl stop wom-server      # detener
journalctl -u wom-server -f         # logs en vivo
```

Reiniciar el servidor **corta las partidas en curso** (no hay persistencia de
partidas): avisá a los jugadores. Las partidas son efímeras; una caída del
servidor las termina (la del host en LAN hacía lo mismo).
