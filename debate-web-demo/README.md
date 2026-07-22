# debate-web-demo

**Throwaway dev tool.** A zero-build browser client for demoing a full live
debate — matchmaking, LiveKit video, and live fact-checking — in **two browser
windows on one laptop**, with no native build, app store account, or second
device. It talks to the same running backend as debate-mobile, using LiveKit's
real browser SDK (`livekit-client` from a CDN).

It deliberately skips everything product-shaped: no age gate, no guidelines,
no report/block UI, no reconnect logic, no styling. **Not** a reference for a
real web client.

## Run

Backend first (all local):

1. `debate-infra`: `make up`
2. `debate-api`: `.venv/bin/uvicorn app.main:app --port 8000`
3. `debate-agent` (optional — only needed for the Fact Check button to work):
   `python agent.py dev` with real `DEEPGRAM_API_KEY` + `GEMINI_API_KEY`, and
   LiveKit creds matching the local dev server (`devkey` / `devsecret_change_me`).

Then, from this folder:

```sh
python3 -m http.server 5500
```

Open <http://localhost:5500>. No build step, no npm install, no Python
dependencies — `http.server` is in the standard library, and any other static
file server works too.

## Demoing a debate on one laptop

1. Open <http://localhost:5500> in **two separate browser profiles** (or one
   normal + one incognito window). **Not two tabs in the same profile** — they
   share `localStorage`, so both tabs would be the same device/user and the
   server will refuse to match you with yourself.
2. Grant camera/mic in both. Both windows will show the same physical camera —
   that's expected on one machine.
3. **Mute one side's mic** (button in the room) or wear headphones — two live
   calls on one laptop feed each speaker's output into the other's mic and it
   will howl.
4. Pick the **same topic** and **opposite stances**, click **Find Match** in
   both. Both land in the same room and see each other's feed.
5. When the chip says **fact-checker: active** (needs debate-agent running),
   speak a factual claim on one side, then press **Fact Check** on the *other*
   side — it checks the opponent's last 30 seconds. A "checking…" notice
   appears, then a verdict card with sources.

## Notes / troubleshooting

- **API base** is editable in the top bar (persists in `localStorage`,
  default `http://localhost:8000`). Changing it reloads the page.
- The LiveKit URL is not configured here — it arrives per-match from the
  server (`match_found.livekit_url`, from debate-api's `LIVEKIT_URL`).
- **Media connects but is black/silent**: the LiveKit dev server advertises
  the `node_ip` baked at `make up` time in debate-infra. If you changed Wi-Fi
  since, run `make down && make up` there.
- **No audio from the opponent**: browsers sometimes block autoplay — a
  "Click to enable audio" button appears; click it.
- **Fact Check button never enables**: debate-agent isn't running (or can't
  reach LiveKit). The debate itself works fine without it.
- **"already in queue" errors**: you probably used two tabs in one profile
  (same device id). Use separate profiles/incognito.
- The server enforces a 10s per-user fact-check cooldown and one in-flight
  check per room; a quick second click just returns a polite error notice.
