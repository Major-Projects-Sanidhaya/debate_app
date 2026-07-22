# Deploying debate-agent to Railway + LiveKit Cloud

The worker has no HTTP surface. It **dials out** to LiveKit over a websocket and
receives jobs on that connection, so it needs no inbound ports, no public
domain, and no port forwarding. That is also why you can run it from your Mac
against LiveKit Cloud before deploying anything — start there.

Deploy [debate-api](../debate-api/DEPLOY.md) first: this worker needs its public
URL and its `INTERNAL_API_KEY`.

---

## 1. Run locally against LiveKit Cloud (do this first)

This is the fastest way to prove your cloud credentials work, with no deploy in
the loop. Point `.env` at LiveKit Cloud instead of the docker dev server:

```sh
# debate-agent/.env
LIVEKIT_URL=wss://your-project.livekit.cloud
LIVEKIT_API_KEY=API...            # LiveKit Cloud → Settings → Keys
LIVEKIT_API_SECRET=...
DEEPGRAM_API_KEY=...
LLM_PROVIDER=gemini
GEMINI_API_KEY=...
REDIS_URL=redis://localhost:6379/0
INTERNAL_API_URL=https://<debate-api-domain>
INTERNAL_API_KEY=<same value as debate-api>
```

```sh
python agent.py dev
```

Your laptop is now a worker for your cloud LiveKit project. Any match created
against that project — including from a phone running the deployed app — gets
dispatched to it. Nothing needs to reach *you*; the connection is outbound.

Keep this mode in your pocket for debugging production: you can point a local
worker at the production LiveKit project and read the logs directly.

> `dev` also enables hot-reload. Production uses `start` (see below).

---

## 2. Railway service

The worker joins the **same Railway project** as debate-api so it can share the
Redis instance.

1. Open your existing Railway project → **+ New → GitHub Repo** → same repo.
2. Service → **Settings**:
   - **Root Directory**: `debate-agent`
   - **Build**: Dockerfile autodetected. No build command.
   - **Networking**: **do not generate a domain.** This is a worker, not a
     server; there is nothing to route to it.
   - **Healthcheck**: leave empty — there is no HTTP endpoint to probe. Railway
     considers the service healthy while the process stays up.
3. **Variables**:

   | Variable | Value |
   |---|---|
   | `ENV` | `production` |
   | `LIVEKIT_URL` | `wss://your-project.livekit.cloud` |
   | `LIVEKIT_API_KEY` | LiveKit Cloud key |
   | `LIVEKIT_API_SECRET` | LiveKit Cloud secret |
   | `DEEPGRAM_API_KEY` | Deepgram console |
   | `LLM_PROVIDER` | `gemini` |
   | `GEMINI_API_KEY` | Google AI Studio |
   | `REDIS_URL` | `${{Redis.REDIS_URL}}` — the same Redis as debate-api |
   | `INTERNAL_API_URL` | `https://<debate-api-domain>` |
   | `INTERNAL_API_KEY` | **exactly** debate-api's value |
   | `MODERATION_TEST_PHRASE` | leave **unset** in production |

   Using `LLM_PROVIDER=anthropic` instead? Set `ANTHROPIC_API_KEY` and drop
   `GEMINI_API_KEY` — only the selected provider's key is required.

4. Deploy.

---

## 3. Confirming it is healthy

The worker is quiet until a match happens. In Railway's logs, look for these
four lines, in order:

**a. Worker registered** — it reached LiveKit Cloud and is accepting jobs:

```json
{"message": "registered worker", "level": "INFO", "name": "livekit.agents", "id": "AW_...", "url": "wss://your-project.livekit.cloud"}
```

**b. Job received** — a match was created and dispatched to this worker:

```json
{"message": "received job request", "level": "INFO", "name": "livekit.agents", "job_id": "AJ_..."}
```

**c. Room metadata parsed** — it read the match context debate-api wrote:

```json
{"event": "joined_room", "topic": "Gun control", "fact_check_mode": "on_demand", "match_id": "...", "room": "match_...", "level": "info"}
```

If instead you see `room_metadata_missing` or `room_metadata_invalid`, the
worker joined a room that debate-api did not create (or an older API version) —
it degrades to on-demand mode with topic `unknown` rather than failing.

**d. Ready** — the fact-check UI's chip turns green when this lands:

```json
{"event": "agent_ready", "match_id": "...", "level": "info"}
```

You should also see `llm_provider_selected` and `moderation_configured` right
after the job starts, echoing which provider and models are live.

---

## 4. Graceful shutdown

On `SIGTERM` (every Railway redeploy sends one):

1. livekit-agents **drains**: the worker stops accepting new jobs while
   in-progress debates keep running. Logged as
   `{"message": "draining worker", "timeout": 300}`.
2. The drain is bounded to **300 seconds** (`DRAIN_TIMEOUT_SECONDS` in
   `config.py`). The library default is 3600s, which would hold a deploy for an
   hour; a debate that has not ended in five minutes is dropped.
3. Each job then runs its shutdown callback, which:
   - cancels the per-track STT and video-sampling loops (they never end on
     their own), then
   - waits up to **5 seconds** (`IO_DRAIN_TIMEOUT_SECONDS`) for in-flight
     moderation POSTs and data-channel publishes to land, then
   - closes the HTTP, Redis, and provider clients, logging
     `job_shutdown_complete`.

That ordering is deliberate: closing the HTTP client first would cancel a
`severity: severe` moderation event mid-flight, and debate-api needs that event
to terminate the match. Anything still unfinished after 5s is cancelled and
logged as `shutdown_io_drain_incomplete` — the internal client already treats a
failed POST as "log and drop", so nothing hangs.

With no active job, shutdown takes about a second.

---

## 5. If it misbehaves

**The deploy crash-loops immediately.** The production guard names every
problem and exits 1 before connecting to anything:

```json
{"event": "fatal_config_error", "problem": "GEMINI_API_KEY is not set, but LLM_PROVIDER=gemini requires it", "env": "production", "level": "error"}
```

It refuses to start when, with `ENV=production`: `LIVEKIT_API_KEY` is missing or
still `devkey`, `LIVEKIT_API_SECRET` is still the dev default, `DEEPGRAM_API_KEY`
is unset, `INTERNAL_API_KEY` is unset or the dev default, or the selected
provider's key is unset.

| Symptom | Likely cause |
|---|---|
| `registered worker` never appears | `LIVEKIT_URL` isn't the `wss://` Cloud URL, or the key/secret belong to a different project |
| Worker registers, but no job on a new match | debate-api is pointed at a different LiveKit project — its `LIVEKIT_URL` must match this one |
| Fact-check chip stays grey in the app | worker never joined; check for a job-received line at the time of the match |
| `moderation_event_dropped` with `http 401` | `INTERNAL_API_KEY` differs from debate-api's |
| `moderation_event_dropped` with a connection error | `INTERNAL_API_URL` wrong, or missing the `https://` scheme |
| `gemini_quota` warnings | free-tier AI Studio limits; switch to a paid key or `LLM_PROVIDER=anthropic` |

**Cost note.** The worker holds an STT stream open for the length of every
debate, so Deepgram is the recurring cost driver, not the LLM calls (those are
rate-limited by the cooldowns and the 72h claim cache).

---

## Running the production image locally

```sh
docker build -t debate-agent:prod .
docker run --rm --env-file .env debate-agent:prod
```

Keep `ENV=development` in that `.env` if it still holds dev LiveKit credentials,
otherwise the guard will (correctly) refuse to start.
