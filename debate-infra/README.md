# debate-infra

Shared local dev infrastructure for the debate app — Postgres, Redis, and a
LiveKit server, plus an optional Prometheus + Grafana monitoring stack. The
app itself lives in separate repos ([debate-api](../debate-api),
[debate-mobile](../debate-mobile), debate-agent later); this repo only
provides the dependencies they connect to.

Everything here is dev-only: the credentials are placeholders and nothing is
hardened for exposure beyond your local network.

## Prerequisites

- macOS with [Docker Desktop](https://www.docker.com/products/docker-desktop/)
  (includes Docker Compose v2)
- `make` — preinstalled once you have the Xcode Command Line Tools
  (`xcode-select --install` if `make` isn't found)

Nothing else. No language runtimes, no package installs.

## Quickstart

```sh
git clone <this-repo> && cd debate-infra
make up
```

`make up` does two things:

1. Detects your Mac's LAN IP and renders `livekit.yaml` from
   `livekit.yaml.template` (see [why this matters](#simulator-vs-physical-device)).
2. Runs `docker compose up -d --wait`, which only returns success once every
   container reports healthy.

Then set up env for the app repos:

```sh
cp .env.example .env    # then set LAN_IP to the output of `make lan-ip`
```

The app repos read these same values (`POSTGRES_URL`, `REDIS_URL`,
`LIVEKIT_URL`, …) — copy the file or the values into each repo's own `.env`.

## Services and ports

| Service    | Port(s)                        | Notes                                  |
| ---------- | ------------------------------ | -------------------------------------- |
| Postgres   | 5432                           | db/user/password all `debate`          |
| Redis      | 6379                           | no auth                                |
| LiveKit    | 7880 (ws/http), 7881 (tcp), 7882 (udp) | key `devkey` / secret `devsecret_change_me` |
| Prometheus | 9090                           | `monitoring` profile only              |
| Grafana    | 3000                           | `monitoring` profile only, `admin`/`admin` |

Host ports for Postgres and Grafana are overridable via `POSTGRES_PORT` /
`GRAFANA_PORT` in `.env` — handy when a locally installed Postgres or another
dev server already owns the default (if you change `POSTGRES_PORT`, update
`POSTGRES_URL` to match).

Make targets: `make help` lists them all (`up`, `down`, `logs`, `psql`,
`redis-cli`, `monitoring`, `lan-ip`, `render`).

## Finding your Mac's LAN IP

```sh
make lan-ip        # runs: ipconfig getifaddr en0
```

`en0` is Wi-Fi on modern Macs; if it prints nothing, try
`ipconfig getifaddr en1`. You can also look in System Settings → Wi-Fi →
Details → IP address.

The IP is assigned by DHCP and changes when you switch networks (and
occasionally on the same network). After it changes, run
`make down && make up` so LiveKit picks up the new address.

## Simulator vs physical device

**The iOS simulator shares your Mac's network stack.** On the simulator,
`localhost` *is* the Mac, so `ws://localhost:7880` reaches LiveKit directly.

**A physical phone is its own machine on the Wi-Fi network.** On the phone,
`localhost` means the phone itself, so it must connect to your Mac's LAN IP
instead: `ws://<LAN_IP>:7880` (this is what `LIVEKIT_URL` in `.env` resolves
to).

There's a second, sneakier half to this: **connecting the websocket is not
enough for media.** The websocket only carries signaling. Audio/video flows
over WebRTC to whatever address LiveKit advertises in its ICE candidates —
and inside Docker, LiveKit only knows its container IP, which nothing outside
Docker can reach. If it advertises that, you get the classic failure mode:
*room joins fine, but no audio or video ever arrives.*

That's why `livekit.yaml` sets `node_ip` to your Mac's LAN IP. The file is
generated: `make render` (run automatically by `make up`) substitutes
`${LAN_IP}` in `livekit.yaml.template` with the detected IP. Don't edit
`livekit.yaml` directly — it's gitignored and overwritten on every `make up`.
To force a specific address: `LAN_IP=192.168.1.42 make up`.

## Monitoring (optional)

```sh
make monitoring    # core services + prometheus + grafana
```

- **Prometheus** — <http://localhost:9090>. Scrapes the FastAPI app at
  `host.docker.internal:8000/metrics`, i.e. debate-api running on your Mac on
  port 8000. Until the api is up, that target shows DOWN — expected.
- **Grafana** — <http://localhost:3000>, login `admin`/`admin`. The
  Prometheus datasource is pre-provisioned; no setup needed.

`make down` stops the monitoring containers too.

## Smoke test

After `make up` (which already waits for health checks):

```sh
docker compose ps
```

All three services should show `healthy`:

```
NAME              ...   STATUS
debate-livekit    ...   Up (healthy)
debate-postgres   ...   Up (healthy)
debate-redis      ...   Up (healthy)
```

LiveKit answers over HTTP:

```sh
curl http://localhost:7880    # → OK
```

Postgres and Redis respond:

```sh
make psql        # \l shows the debate database; \q to exit
make redis-cli   # PING → PONG; exit with ctrl-d
```

Optional device check: open `http://<LAN_IP>:7880` in the phone's browser —
seeing `OK` proves the phone can reach your Mac at all.

## Troubleshooting

- **Room connects but no audio/video** — LiveKit is advertising a stale IP.
  Check `node_ip` in the generated `livekit.yaml`, then `make down && make up`.
- **Phone can't connect at all** — confirm phone and Mac are on the same
  Wi-Fi. Many office/guest networks enable AP isolation, which blocks
  device-to-device traffic; a personal hotspot is a workaround.
- **`make up` fails on `--wait`** — `make logs` to see which container is
  unhealthy. A common cause is a port conflict, e.g. a locally installed
  Postgres already on 5432 (`lsof -iTCP:5432 -sTCP:LISTEN`); set
  `POSTGRES_PORT=5433` in `.env` and update `POSTGRES_URL` to match.
- **Bare `docker compose up` fails on a clean clone** — `livekit.yaml`
  doesn't exist yet; it's generated. Use `make up` (or `make render` first).
- **Full reset (wipes Postgres/Grafana data):**
  `docker compose --profile monitoring down -v`
