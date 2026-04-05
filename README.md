# Homeserver Monitoring

Full observability stack for `kraken` (192.168.1.30) — a self-hosted homeserver running Docker-based services, Pi-hole DNS, and multiple storage drives.

Accessible from anywhere on the LAN at **http://192.168.1.30:3001**

---

## What's monitored

| Category | Tool | Detail |
|---|---|---|
| System | node-exporter | CPU per-core, RAM, load avg, uptime |
| Disks | node-exporter | `/`, MEDIA (1.8T), MEDIA2 (916G), MEDIA3 (4.6T), `/mnt/plex` |
| Disk I/O | node-exporter | Read/write throughput per device |
| Network | node-exporter | RX/TX on physical interfaces |
| Docker | cAdvisor | Per-container CPU %, memory, real-time and historical |
| Services | blackbox-exporter | HTTP probe — UP/DOWN + response time for every service |
| Pi-hole | pihole-exporter | Query rate, block rate, % blocked, domains on blocklist |

### Services probed for uptime

| Service | Port | Notes |
|---|---|---|
| Pi-hole | 80 | DNS ad-blocker |
| qBittorrent | 9001 | Torrent client WebUI |
| Omni-Tools | 9002 | |
| CyberChef | 9003 | |
| IT-Tools | 9004 | |
| Stirling-PDF | 9005 | |
| ConvertX | 9006 | |
| Homepage | 3000 | Currently exited — shows red when down |
| Homebridge | 8581 | HomeKit bridge |

---

## Stack

```
Grafana :3001          — dashboard UI, provisioned from files (no manual setup)
Prometheus :9090       — time series DB, scrapes all exporters every 15s, retains 30 days
node-exporter :9100    — system metrics (host network)
cAdvisor :8080         — docker container metrics (internal only)
blackbox-exporter :9115 — HTTP probe engine (internal only)
pihole-exporter :9617  — Pi-hole API to Prometheus bridge (internal only)
```

---

## Directory structure

```
monitoring/
├── docker-compose.yml
├── .env.example                          ← template — copy to .env
├── .gitignore
├── prometheus/
│   └── prometheus.yml                    ← scrape targets and relabeling
├── blackbox/
│   └── blackbox.yml                      ← HTTP probe modules
└── grafana/
    └── provisioning/
        ├── datasources/
        │   └── prometheus.yml            ← auto-wires Prometheus datasource (uid: prometheus)
        └── dashboards/
            ├── dashboard.yml             ← tells Grafana to load JSON files from this dir
            └── homeserver.json           ← the full dashboard definition
```

Everything is code — no manual Grafana configuration is ever needed. On a fresh deploy the dashboard appears automatically.

---

## Fresh setup (server nuked)

### 1. Prerequisites

- Docker 20.10+ and Docker Compose v2
- `gh` CLI authenticated (for pushing changes back)
- The other services (`pihole`, `qbittorrent`, etc.) should already be running for their probes to show green, but monitoring works without them

Install Docker if missing:

```bash
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER
newgrp docker
```

### 2. Clone the repo

```bash
git clone https://github.com/AdityaHarindar/homeserver-monitoring.git
cd homeserver-monitoring
```

### 3. Create the .env file

```bash
cp .env.example .env
```

Edit `.env`:

```env
GRAFANA_PASSWORD=pick_a_strong_password
PIHOLE_PASSWORD=<value of FTLCONF_webserver_api_password from selfhosted/pihole/docker-compose.yml>
```

The Pi-hole password is the value set in the pihole container's `FTLCONF_webserver_api_password` environment variable. The pihole-exporter uses it to authenticate against Pi-hole's HTTP API.

### 4. Start the stack

```bash
docker compose up -d
```

All six containers start. Grafana is live at **http://192.168.1.30:3001** within ~10 seconds.

Default login: `admin` / value of `GRAFANA_PASSWORD` from your `.env`.

### 5. Verify everything is scraping

Open **http://192.168.1.30:9090/targets** in a browser. Every target should show state = `UP`.

If `node-exporter` shows `Unknown` for a few seconds after startup, that's normal — wait one scrape interval (15s).

---

## How it works

### Why node-exporter runs on the host network

node-exporter needs to see real network interfaces (`eth0`, not the container's virtual `eth0`). Running with `network_mode: host` gives it direct access to the host's network stack. It also runs with `pid: host` so it can read `/proc` entries for all processes, not just its own container.

The flags `--path.procfs`, `--path.sysfs`, and `--path.rootfs` redirect node-exporter to read from the bind-mounted host paths instead of the container's own `/proc`, `/sys`, `/`. The `--path.rootfs=/host/root` flag is what lets it report real host filesystem usage — **but it strips the `/host/root` prefix when reporting mountpoints**, so metrics show `/media/aditya/MEDIA`, not `/host/root/media/aditya/MEDIA`.

### Why `host.docker.internal` is used

Prometheus, blackbox-exporter, and pihole-exporter all run on the `monitoring` bridge network. They need to reach services running on the host (node-exporter on :9100, Pi-hole on :80, and all the other services the blackbox probes). On Linux, `host.docker.internal` is not available by default — the `extra_hosts: ["host.docker.internal:host-gateway"]` entry in the compose file injects it as an `/etc/hosts` entry pointing at the Docker bridge gateway IP, which routes to the host.

### How blackbox probing works

The blackbox exporter is not scraped directly for service metrics. Instead, Prometheus's scrape config lists the URLs to probe as targets, then a `relabel_configs` block rewrites the request: the URL becomes a query parameter (`?target=http://host.docker.internal:9001`) and the actual scrape address is rewritten to `blackbox-exporter:9115`. So Prometheus tells blackbox "please probe this URL" and gets back the result. The `service` label is added via relabeling so panel legends show names instead of raw URLs.

The `http_up` module accepts any HTTP response including 401/403/302 — the service is considered up if it responds at all. This matters because services like qBittorrent redirect to a login page (302/401) rather than returning 200.

### Why the datasource has `uid: prometheus`

Grafana auto-generates a random UID for a datasource if one isn't specified in provisioning. The dashboard JSON references the datasource by UID in every panel. If the UID doesn't match, every panel silently shows "No data". Setting `uid: prometheus` in `provisioning/datasources/prometheus.yml` makes this deterministic — the dashboard JSON can safely hardcode it.

---

## Storage reference

| Drive | Device | Mount | Size | Notes |
|---|---|---|---|---|
| NVMe (OS) | nvme0n1p2 | `/` | 228G | Root filesystem |
| MEDIA | sda1 | `/media/aditya/MEDIA` | 1.8T | |
| MEDIA2 | sdc2 | `/media/aditya/MEDIA2` | 916G | ~78% full — watch this one |
| MEDIA3 | sdb2 | `/media/aditya/MEDIA3` | 4.6T | |
| Plex | (network) | `/mnt/plex` | 7.2T | Network-mounted |

The storage gauges in the dashboard turn yellow at 70% and red at 90%. MEDIA2 is currently at ~78% — yellow.

---

## Changing what's monitored

### Add a new service to uptime probing

1. Add its URL to `prometheus/prometheus.yml` under `job_name: blackbox-http` → `targets`:
   ```yaml
   - http://host.docker.internal:XXXX
   ```
2. Add a `service` label for it in the `relabel_configs` block below (copy the pattern of the existing entries).
3. Reload Prometheus without restarting:
   ```bash
   curl -X POST http://localhost:9090/-/reload
   ```
   The new target appears in Grafana within one scrape interval.

### Change the Grafana password

Update `GRAFANA_PASSWORD` in `.env`, then:
```bash
docker compose up -d grafana
```

### Change the Pi-hole password

If you reset Pi-hole's web password, update `PIHOLE_PASSWORD` in `.env`, then:
```bash
docker compose up -d pihole-exporter
```

### Change data retention

Edit `docker-compose.yml`, find the prometheus `command:` block, change:
```yaml
- '--storage.tsdb.retention.time=30d'
```
Then `docker compose up -d prometheus`.

---

## Ports used by this stack

| Port | Container | Exposed |
|---|---|---|
| 3001 | Grafana | Yes — LAN access |
| 9090 | Prometheus | Yes — query UI + API |
| 9100 | node-exporter | Yes (host network) — internal |
| 9115 | blackbox-exporter | No — internal bridge |
| 8080 | cAdvisor | No — internal bridge |
| 9617 | pihole-exporter | No — internal bridge |

---

## Useful commands

```bash
# Start everything
docker compose up -d

# Stop everything
docker compose down

# Tail logs for a specific container
docker compose logs -f grafana
docker compose logs -f prometheus

# Check all scrape targets
curl http://localhost:9090/api/v1/targets | python3 -m json.tool

# Force Prometheus to reload config (after editing prometheus.yml)
curl -X POST http://localhost:9090/-/reload

# Reset Grafana admin password if locked out
docker compose exec grafana grafana cli admin reset-admin-password newpassword

# Wipe Grafana state and re-provision from scratch (dashboard + datasource)
docker compose stop grafana
docker compose rm -f grafana
docker volume rm monitoring_grafana_data
docker compose up -d grafana
```

---

## Troubleshooting

**All panels show "No data"**
- The datasource UID in Grafana doesn't match the dashboard JSON. This happens if you restored a Grafana volume from a different install. Fix: wipe and re-provision (see command above).
- Check Prometheus is reachable: `curl http://localhost:9090/-/healthy`

**node-exporter target is down**
- It runs on host network (no Docker network). Check: `curl http://localhost:9100/metrics | head -5`
- If port 9100 is blocked: check `sudo ufw status`

**Filesystem gauges show "No data" for a drive**
- The mountpoint doesn't exist yet (drive not mounted). Check: `df -h`
- Edit the gauge query in `grafana/provisioning/dashboards/homeserver.json` to match the actual mountpoint, then wipe and re-provision Grafana.

**Pi-hole panels show "No data"**
- pihole-exporter can't reach Pi-hole. Check: `docker compose logs pihole-exporter`
- If Pi-hole's password changed, update `PIHOLE_PASSWORD` in `.env` and `docker compose up -d pihole-exporter`

**A service shows DOWN in the services panel**
- Expected if the service is actually stopped (e.g. Homepage has been exited for 8 months — it shows red)
- To stop monitoring a service that no longer exists: remove it from the `targets` list in `prometheus/prometheus.yml` and reload Prometheus
