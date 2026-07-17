# debate-api

Matchmaking and auth microservice for the debate app. Users auth anonymously
by device, pick a topic and stance, and get paired over a websocket with
someone holding the opposite stance; the service mints LiveKit tokens and both
clients join the same video room. Postgres, Redis, and a LiveKit dev server
come from [debate-infra](../debate-infra); the Expo client lives in
debate-mobile and is built against the exact API contract below.

## Stack

Python 3.12 · FastAPI · SQLAlchemy 2 async + asyncpg · Alembic · redis-py
(async, Lua scripting) · livekit-api · PyJWT · structlog (JSON logs with
request IDs) · prometheus-fastapi-instrumentator (`/metrics`) · pytest +
testcontainers · ruff

## Quickstart

```sh
# 1. dependencies (Python 3.12)
python3.12 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# 2. env — defaults match debate-infra; set LIVEKIT_URL to your LAN IP
cp .env.example .env

# 3. schema + topics (debate-infra must be up: `make up` there)
alembic upgrade head
python -m scripts.seed

# 4. run
uvicorn app.main:app --reload --port 8000
```

Check: `curl localhost:8000/healthz` → `{"status":"ok"}` (verifies DB and
Redis connectivity). Prometheus metrics are on `/metrics`; debate-infra's
monitoring profile scrapes them.

### Two-client acceptance demo

With the server running:

```sh
python -m scripts.two_client_demo
```

Auths two devices, joins them pro/con on the first topic, prints both
`match_found` payloads (including decoded LiveKit token claims), and ends the
match. Exits non-zero if anything doesn't match the contract.

## API contract (mirrored by debate-mobile — do not change shapes)

- `POST /auth/device` `{"device_id": "<uuid>", "over_18": true}` →
  `201 {"token": "<jwt>", "user_id": "<uuid>"}`. Idempotent per `device_id`.
  403 if `over_18` is not true or the user is banned. The JWT (HS256, `sub` =
  user id, 30-day expiry) goes in `Authorization: Bearer` for everything else.
- `GET /topics` → `200 [{"id": 1, "title": "Gun control"}, ...]`
- `WS /ws/match?token=<jwt>` — token in the query string because browser
  WebSocket clients can't set headers.
  - client→server: `{"type":"join","topic_id":1,"stance":"pro|con","fact_check_mode":"on_demand|auto"}`,
    `{"type":"cancel"}`
  - server→client: `{"type":"queued"}` ·
    `{"type":"match_found","match_id","room_name":"match_<id>","livekit_url","livekit_token","topic":{"id","title"},"your_stance","peer_stance","fact_check_mode"}` ·
    `{"type":"error","message"}`
  - `fact_check_mode` resolves to `"auto"` only if **both** users chose auto.
- `POST /matches/{match_id}/end` → 204, idempotent, either participant.
- `GET /healthz` → `{"status":"ok"}` (no auth).

## Matchmaking design

Queues live in Redis: `q:{topic_id}:{stance}` lists hold waiting user ids, and
`inq:{user_id}` marks queue membership (value: `<queue_key>|<fact_check_mode>`,
which is how each user's requested mode is stored alongside their entry).
Pair-or-enqueue is a single Lua script — one atomic step that pops the
opposite-stance queue (discarding stale entries whose `inq` is missing or
points elsewhere, and guarding against self-match) or enqueues the caller —
so it stays correct with multiple API replicas racing on the same topic.

`inq` keys carry a 5-minute TTL, refreshed in the background while the owning
socket is queued. If a replica dies without cleanup, its queue state
self-heals. Cancel and websocket disconnect run the same removal script
(LREM + DEL), and a user can be in at most one queue: double joins get an
error.

On match the popper inserts the `matches` row, mints two LiveKit tokens
(identity = user id, name = stance, join grant for `match_<match_id>`, 2h
TTL), and pushes `match_found` to both sockets through an **in-process**
connection manager. Every match creation is logged with `match_id`, topic,
and mode.

### LiveKit room + metadata (consumed by debate-agent)

At match time — before `match_found` goes out — the server explicitly creates
the LiveKit room (`match_<match_id>`, `empty_timeout: 300`) via RoomService,
with metadata debate-agent parses verbatim to discover match context:

```json
{"match_id":"<uuid>","topic_id":1,"topic":"Gun control",
 "fact_check_mode":"on_demand"|"auto",
 "user_pro":"<uuid>","user_con":"<uuid>"}
```

If the room already exists it's treated as success (warning logged), and any
room-creation failure is logged but never blocks `match_found` — users can
debate without an agent. See `app/livekit_rooms.py`.

**Known scope limits (accepted for local dev):** `match_found` delivery is
in-process, so both users' sockets must be on the same replica — the Redis
side is replica-safe, delivery is not (a shared bus, e.g. Redis pub/sub,
would fix this). Queue keys are computed inside the Lua scripts, which is
incompatible with Redis Cluster hash slots.

## Database

`users(id, device_id unique, banned, created_at)` ·
`topics(id, title unique, active)` ·
`matches(id, topic_id, user_pro, user_con, fact_check_mode, started_at,
ended_at)` — see `alembic/versions/0001_initial.py`. `python -m scripts.seed`
inserts 10 contested topics, idempotently.

## Tests

```sh
pytest
```

Runs against **real** Postgres 16 and Redis 7 via testcontainers, so Docker
must be running (the images are the same ones debate-infra uses, so they're
already pulled). The suite migrates and seeds the throwaway database, then
covers: device-auth idempotency, banned users, pro+con matching, same-stance
and cross-topic non-matching, double-join rejection, disconnect/cancel queue
removal, stale-entry cleanup, self-match guard, both fact-check-mode
resolutions, LiveKit token validity, and match ending.

Lint: `ruff check .`

## Docker

```sh
docker build -t debate-api .
docker run --rm -p 8000:8000 \
  -e POSTGRES_URL=postgresql+asyncpg://debate:debate@host.docker.internal:5432/debate \
  -e REDIS_URL=redis://host.docker.internal:6379/0 \
  -e LIVEKIT_URL=ws://<LAN_IP>:7880 \
  debate-api
```

(Local dev is normally `uvicorn` on the host — that's what debate-infra's
Prometheus scrape target expects.)
