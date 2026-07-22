# Deploying debate-api to Railway + LiveKit Cloud

A first-deploy runbook. Nothing here changes how the API behaves — same routes,
same WS messages, same room metadata, same matchmaking.

You need: a GitHub repo containing this directory, a [Railway](https://railway.app)
account, and a [LiveKit Cloud](https://cloud.livekit.io) account. Both have free
tiers that fit this app.

---

## 1. LiveKit Cloud

1. Sign in at <https://cloud.livekit.io> and **Create Project**.
2. Open **Settings → Keys** and create an API key (or use the default one).
3. Copy three values — you'll paste them into Railway in step 3:
   - **Project URL** — looks like `wss://your-project.livekit.cloud`
   - **API Key** — starts with `API...`
   - **API Secret**

> The dev server's `devkey` / `devsecret_change_me` will not start in
> production — the app refuses to boot with them (see step 6).

---

## 2. Railway project, Postgres, and Redis

1. <https://railway.app> → **New Project** → **Empty Project**.
2. **+ New → Database → Add PostgreSQL**.
3. **+ New → Database → Add Redis**.

Leave both alone; Railway manages their credentials and exposes them as
variables you'll reference in step 3.

---

## 3. The API service

1. **+ New → GitHub Repo** and pick your repo. Authorize Railway if asked.
2. Open the new service → **Settings**:
   - **Root Directory**: `debate-api`
     *(required — the repo holds four services; this points Railway at this one)*
   - **Build**: Railway autodetects the `Dockerfile`. No build command needed.
   - **Deploy → Healthcheck Path**: `/healthz`
3. **Variables** tab → add these. For the two database URLs use Railway's
   variable references so they track credential rotations automatically:

   | Variable | Value |
   |---|---|
   | `POSTGRES_URL` | `${{Postgres.DATABASE_URL}}` |
   | `REDIS_URL` | `${{Redis.REDIS_URL}}` |
   | `LIVEKIT_URL` | `wss://your-project.livekit.cloud` |
   | `LIVEKIT_API_KEY` | from LiveKit Cloud |
   | `LIVEKIT_API_SECRET` | from LiveKit Cloud |
   | `ENV` | `production` |
   | `JWT_SECRET` | generate — see below |
   | `INTERNAL_API_KEY` | generate — see below |
   | `CORS_ORIGINS` | `*` for now (see step 7) |

   Generate the two secrets locally and paste the output:

   ```sh
   openssl rand -hex 32   # -> JWT_SECRET
   openssl rand -hex 32   # -> INTERNAL_API_KEY
   ```

   Use **different** values for the two. `INTERNAL_API_KEY` must match the one
   you give debate-agent, or its moderation events get 401s.

   > `POSTGRES_URL` is optional if you'd rather rely on Railway's own
   > `DATABASE_URL` — the app reads either, and prefers `POSTGRES_URL`.
   > Railway's URL arrives as `postgresql://…` (sometimes with `?sslmode=require`);
   > the app rewrites it to `postgresql+asyncpg://` and translates `sslmode`
   > into the form asyncpg accepts. Nothing to do by hand.

4. **Settings → Networking → Generate Domain**. Note the
   `https://<something>.up.railway.app` domain.
5. Deploy. Railway builds the Dockerfile and starts the container.

---

## 4. What happens on each deploy

`docker-entrypoint.sh` runs, in order:

1. **Config preflight** — refuses to start (exit 1, JSON error log) if any
   production credential is missing or still a dev default. This runs *before*
   the database is touched.
2. `alembic upgrade head` — idempotent.
3. `python scripts/seed.py` — idempotent; inserts the 10 topics once.
4. `exec uvicorn app.main:app --host 0.0.0.0 --port $PORT` — Railway injects
   `PORT`.

> ⚠️ **Single-replica assumption.** Migrations run on every container start,
> which is safe for one replica and for rolling restarts of one replica. If you
> scale to more than one replica, two containers can run `alembic upgrade head`
> at the same time. Before scaling out: move the migrate + seed lines into a
> Railway **pre-deploy command** and leave only the `exec uvicorn` line in the
> entrypoint.

Logs are JSON on stdout (structlog); nothing is written to a file inside the
container. Railway's log viewer shows them as-is.

---

## 5. Smoke test the deployment

Replace `<domain>` with your Railway domain.

```sh
# 1. Health — checks Postgres and Redis connectivity too
curl https://<domain>/healthz
# {"status":"ok"}

# 2. Device auth
curl -X POST https://<domain>/auth/device \
  -H 'Content-Type: application/json' \
  -d '{"device_id":"11111111-1111-1111-1111-111111111111","over_18":true}'
# 201 {"token":"...","user_id":"..."}
```

Then the full matchmaking round-trip. The demo scripts read `API_BASE` and
derive `wss://` from its scheme, so the same scripts that test localhost test
production. Real environment variables take precedence over your local `.env`,
so pass the LiveKit Cloud credentials inline — the script verifies room
metadata directly against LiveKit:

```sh
cd debate-api
API_BASE=https://<domain> \
LIVEKIT_URL=wss://your-project.livekit.cloud \
LIVEKIT_API_KEY=API... \
LIVEKIT_API_SECRET=... \
  python -m scripts.two_client_demo
```

Watch the Railway logs while it runs; you should see the match-creation line:

```json
{"event": "match_created", "match_id": "...", "topic": "Gun control", "fact_check_mode": "on_demand", ...}
```

Block exclusion needs no LiveKit credentials:

```sh
API_BASE=https://<domain> python -m scripts.block_demo
```

---

## 6. If the deploy crash-loops

Check the deploy logs first. The preflight prints exactly what's wrong:

```json
{"event": "fatal_config_error", "problem": "JWT_SECRET is only 12 characters (minimum 32) — generate one with: openssl rand -hex 32", "env": "production", "level": "error"}
```

It refuses to start when, with `ENV=production`:

- `JWT_SECRET` is missing, under 32 characters, or the dev default
- `LIVEKIT_API_KEY` / `LIVEKIT_API_SECRET` are still the dev server's values
- `INTERNAL_API_KEY` is unset or still the dev default

Other common causes:

| Symptom | Cause |
|---|---|
| Healthcheck fails, logs show a Postgres connect error | `POSTGRES_URL` not referencing the Railway Postgres service |
| `TypeError: connect() got an unexpected keyword argument 'sslmode'` | An older build without URL normalization — redeploy from `main` |
| Clients connect but get no audio/video | `LIVEKIT_URL` still points at a local/dev server |
| debate-agent's moderation events return 401 | `INTERNAL_API_KEY` differs between the two services |

---

## 7. After the first successful deploy

- **Lock down CORS.** `CORS_ORIGINS=*` is fine while the only client is the
  Expo app (native apps don't enforce CORS), but set it to your real web
  origins before shipping a web client. With `*`, credentialed requests are
  disabled automatically.
- **Point debate-mobile at the deployment**: `EXPO_PUBLIC_API_URL=https://<domain>`
  (restart Metro — the value is inlined at bundle time).
- **Deploy debate-agent** with `INTERNAL_API_URL=https://<domain>`, the same
  `INTERNAL_API_KEY`, and the same LiveKit Cloud credentials.
- **Back up Postgres.** Railway can snapshot the volume; the `matches`,
  `reports`, and `moderation_events` tables are your moderation audit trail.

---

## Running the production image locally

Useful for reproducing a deploy problem without pushing:

```sh
docker build -t debate-api:prod .
docker run --rm --env-file .env.docker -p 8080:8080 debate-api:prod
curl http://localhost:8080/healthz
```

Point `.env.docker` at the local docker-compose stack with
`host.docker.internal` instead of `localhost`, and keep `ENV=development` so
the production guards don't reject the dev credentials.
