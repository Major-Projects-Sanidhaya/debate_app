# debate-agent

AI fact-checking worker for the debate app. A [LiveKit Agents](https://docs.livekit.io/agents/)
worker joins every match room as the hidden participant **`fc-agent`**, transcribes both
debaters in real time (Deepgram streaming STT), extracts checkable claims and verifies them
against the web via Anthropic's API, and publishes verdicts over the room data channel
(topic `fact_check`). LiveKit + Redis come from [debate-infra](../debate-infra); rooms and
metadata come from [debate-api](../debate-api); [debate-mobile](../debate-mobile) renders
the verdicts.

## Setup (Python 3.10)

```sh
python3.10 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env    # fill in DEEPGRAM_API_KEY and ANTHROPIC_API_KEY
```

**Run this first** — validates both prompts and your Anthropic key with zero
LiveKit/Deepgram involvement (extraction → web-search verification on a mock
transcript, printing verdict JSON):

```sh
python scripts/pipeline_demo.py
```

Then, with debate-infra up (`make up` there):

```sh
python agent.py dev
```

The worker registers with LiveKit and auto-dispatches into every new room. Create a
match (e.g. `python -m scripts.two_client_demo` in debate-api) and watch it join.
`scripts/room_probe.py` smoke-tests the room layer without needing real STT/LLM keys.

## Architecture

```
LiveKit room (match_<id>, metadata set by debate-api)
   │ audio tracks (pro / con)          │ data topic "fact_check"
   ▼                                   ▲
agent.py ── Deepgram STT (per track) ──┤   {"agent_ready"} / status / verdicts / errors
   │  FINAL segments, attributed       │
   ▼                                   │
session.py  DebateSession ─────────────┘
   ├─ RollingTranscript (+ Redis mirror transcript:{match_id}, 24h TTL)
   ├─ rate limits: 10s per-user cooldown, 1 in-flight per room,
   │               auto mode: ≥20s per speaker, drop (never queue)
   └─ pipeline/
        ├─ extraction.py   claude-haiku-4-5-20251001 → ≤2 self-contained claims (strict JSON)
        ├─ verification.py claude-sonnet-4-6 + web_search (max 3) → verdict JSON,
        │                  25s timeout, 1 retry
        └─ cache.py        claim_cache:{sha256(normalized)} — 72h, never "unverifiable"
```

- **Stance attribution:** participant `name` is `"pro"`/`"con"` (set by debate-api's
  tokens); fallback matches identity against `user_pro`/`user_con` from room metadata.
- **Missing/invalid metadata** degrades to on-demand mode with topic `"unknown"`.
- **On-demand** (both modes): a `fact_check_request` from participant X checks the
  *opponent's* last 30s. **Auto** (only when metadata says `auto`): each finalized
  segment considers that *speaker's* last 20s. Auto failures stay in logs — no error
  spam on the data channel.
- `session.py` and `pipeline/` import no LiveKit — the whole decision core is
  unit-testable (`pytest`, 40 tests, all external services mocked).
- Every check logs `match_id`, mode, claim hash, cache hit/miss, verdict, latency,
  and Anthropic token usage as JSON (structlog) for cost tracking.

The data-channel and room-metadata contracts are implemented verbatim from the spec —
field names must not change (debate-mobile parses them exactly).

## Cost notes

Per fact-check (typical):

- **Extraction** — Haiku 4.5 ($1/$5 per MTok): ~500 in / ~60 out ≈ **$0.001**
- **Verification** — Sonnet 4.6 ($3/$15 per MTok): ~2–6K in (search results) / ~300 out,
  plus web search at $10 per 1,000 searches (≤3 per claim) ≈ **$0.02–0.05 per claim**
- Up to 2 claims per check → worst case ~**$0.10 per on-demand request**

What keeps this bounded: the 10s per-user cooldown, one in-flight check per room,
auto mode's 20s per-speaker gap with drop-don't-queue, and the 72h claim cache
(repeated talking points across matches are free). The `web_search_20250305` tool
variant is pinned by the contract; Sonnet 4.6 also supports the newer
`web_search_20260209` (dynamic filtering) if the contract is ever revised.

If a pinned model id ever returns 404, the agent logs it and emits
`fact_check_error` — it never silently substitutes a different model tier. Check
docs.claude.com for current ids before changing `pipeline/extraction.py` /
`pipeline/verification.py`.

## Tests

```sh
pytest
```

Covers: normalization/hashing, cache roundtrip + never-cache-unverifiable, metadata
parsing incl. fallback, strict-JSON re-ask-once-then-error, verification timeout/retry,
model-404 no-substitution, cooldown, single-flight, empty-window and non-participant
errors, auto-mode per-speaker gating and inflight drops, and the full on-demand flow
(status → verdict message shapes).
