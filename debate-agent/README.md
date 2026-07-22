# debate-agent

AI fact-checking worker for the debate app. A [LiveKit Agents](https://docs.livekit.io/agents/)
worker joins every match room as the hidden participant **`fc-agent`**, transcribes both
debaters in real time (Deepgram streaming STT), extracts checkable claims and verifies them
against the web via a pluggable LLM provider — **Google Gemini (free AI Studio tier) by
default, Anthropic selectable** — and publishes verdicts over the room data channel
(topic `fact_check`). LiveKit + Redis come from [debate-infra](../debate-infra); rooms and
metadata come from [debate-api](../debate-api); [debate-mobile](../debate-mobile) renders
the verdicts.

## Setup (Python 3.10)

```sh
python3.10 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env    # fill in DEEPGRAM_API_KEY and GEMINI_API_KEY
```

**Run this first** — validates prompts and your LLM key with zero LiveKit/Deepgram
involvement (extraction → search-grounded verification on a mock transcript; prints
the active provider and verdict JSON):

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

## LLM providers

`LLM_PROVIDER` selects the implementation behind `pipeline/providers/`:

| | `gemini` (default) | `anthropic` |
|---|---|---|
| Extraction | `gemini-2.5-flash-lite`, native JSON schema output | `claude-haiku-4-5-20251001`, strict-JSON prompt |
| Verification | `gemini-2.5-flash` + Google Search grounding | `claude-sonnet-4-6` + `web_search` tool |
| Sources | grounding metadata (Google redirect URIs, titles shown); model JSON as fallback | model-reported search results |
| Key | `GEMINI_API_KEY` — free at [aistudio.google.com/apikey](https://aistudio.google.com/apikey) | `ANTHROPIC_API_KEY` |

- **Switching back to Anthropic is one line in `.env`:** `LLM_PROVIDER=anthropic`
  (with `ANTHROPIC_API_KEY` set). Nothing else changes — session logic, contracts,
  cooldowns, cache, and logging fields are identical across providers.
- **Free-tier privacy note:** on the AI Studio free tier, Google may use prompts and
  responses for product improvement. Fine for testing with mock/dev debates; switch
  to a paid Gemini key or `LLM_PROVIDER=anthropic` before handling anything sensitive.
- **Free-tier quotas:** on 429 the agent retries once when Google names a retry delay
  ≤ 10s, otherwise surfaces "Fact-checker is busy". Quota events are logged as
  `event="gemini_quota"` so free-tier limits stay visible.
- Gemini model ids are overridable via `GEMINI_EXTRACTION_MODEL` /
  `GEMINI_VERIFICATION_MODEL`. If an id 404s, the agent logs it and errors — it never
  silently substitutes; list models via the API or check
  [ai.google.dev](https://ai.google.dev/gemini-api/docs/models) (Anthropic path:
  docs.claude.com).

## Content moderation

Alongside fact-checking, the agent screens each debate for policy violations and
reports them to debate-api's internal intake
(`POST {INTERNAL_API_URL}/internal/moderation/events`, header `X-Internal-Key`).
debate-api decides what to do — a `severity: "severe"` event terminates the match
(LiveKit room deleted, `ended_reason='moderation'`, offender flagged).

**Moderation never delays or breaks fact-checking.** Screening runs in
fire-and-forget tasks off the STT path, and every failure — bad model output,
transport error, a down debate-api — is logged and swallowed, never raised into
the session.

- **Transcript screening** — at most one call per speaker per 15s, classifying
  that speaker's last ~30s. Heated disagreement, insults about *ideas*, and
  profanity are explicitly not violations; the screen looks for targeted
  harassment/hate, sexual content, anything involving minors, credible violent
  threats, and self-harm risk. `medium` posts an event with a ≤300-char excerpt;
  `severe` posts one and debate-api ends the match. Malformed model output gets
  one re-ask, then the screening is dropped and logged.
- **Video screening** — one frame per `VIDEO_SAMPLE_INTERVAL` seconds per
  speaker, downscaled to ≤512px JPEG and classified for sexual content or
  violence/gore (`violence_gore` maps to the contract's `violence_threat`).
  Events carry an empty excerpt and `source: "video"`. Set
  `VIDEO_MODERATION_ENABLED=false` to skip it entirely — the agent then
  subscribes to audio only.
- **Privacy hard rule** — frames are analyzed in memory and discarded. Nothing
  writes a frame to disk, Redis, or logs; moderation logs carry only
  `{match_id, stance, category, severity, latency, ts}`.
- **Model** — moderation goes through the active provider's `complete_json`, so
  it follows `LLM_PROVIDER` like everything else. On `LLM_PROVIDER=anthropic`
  that is `claude-haiku-4-5-20251001` for both text and vision; on the `gemini`
  default it is the provider's configured fast model (keeps dev on the free
  tier). Switch providers to move moderation with it.

### Dev test hook

Setting `MODERATION_TEST_PHRASE` makes any finalized transcript segment
containing that phrase (case-insensitive) synthesize a **severe** `test` event
immediately, with no model call and no debounce — the fastest way to exercise
match termination end to end:

```sh
MODERATION_TEST_PHRASE="banana protocol" python agent.py dev
# say "banana protocol" in a live debate -> match terminates within seconds
cd ../debate-api && python -m scripts.moderate list-events   # the event lands here
```

> **This is a dev-only backdoor: anyone who says the phrase kills the match.
> `MODERATION_TEST_PHRASE` must be blank/unset in production.** The agent logs
> `moderation_test_phrase_active` at startup whenever it is set.

## Deploying

See **[DEPLOY.md](DEPLOY.md)** for the Railway + LiveKit Cloud runbook. Two
things worth knowing first:

- **The worker dials out.** It opens a websocket to LiveKit and receives jobs on
  it — no inbound ports, no public domain. So you can run `python agent.py dev`
  on your laptop against LiveKit Cloud and serve real matches before deploying
  anything.
- **`ENV=production` refuses to start** on dev LiveKit credentials, a missing
  `DEEPGRAM_API_KEY` or `INTERNAL_API_KEY`, or a missing key for the *selected*
  provider (`GEMINI_API_KEY` when `LLM_PROVIDER=gemini`, `ANTHROPIC_API_KEY`
  when `anthropic`). Each problem is logged as JSON before the process exits 1.

Production runs `python agent.py start` (the Dockerfile CMD); `dev` adds
hot-reload. On SIGTERM the worker drains for up to 5 minutes, then each job
flushes in-flight moderation POSTs before closing its clients.

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
   └─ pipeline/providers/       LLM_PROVIDER: gemini (default) | anthropic
        ├─ base.py              interface: extract_claims / verify_claim /
        │                       complete_json (generic strict-JSON + optional image —
        │                       the moderation screening will reuse this)
        ├─ gemini_provider.py   structured-output extraction; Search-grounded
        │                       verification (JSON-in-text + one re-ask: the API
        │                       can't combine JSON mode with grounding)
        ├─ anthropic_provider.py  the original implementation, moved unchanged
        └─ ../cache.py          claim_cache:{sha256(normalized)} — 72h, never "unverifiable"

moderation/  (fire-and-forget, never blocks the above)
   ├─ moderator.py       15s/speaker transcript debounce, video sample gate,
   │                     dev test-phrase hook, event posting
   ├─ classifier.py      text + vision screening via provider.complete_json,
   │                     strict JSON with one re-ask then drop
   ├─ frames.py          rtc frame -> <=512px JPEG in memory (never persisted)
   ├─ internal_client.py POST /internal/moderation/events, X-Internal-Key,
   │                     retry once then log and drop; never raises
   └─ config.py          INTERNAL_API_*, MODERATION_TEST_PHRASE, VIDEO_*
```

- **Stance attribution:** participant `name` is `"pro"`/`"con"` (set by debate-api's
  tokens); fallback matches identity against `user_pro`/`user_con` from room metadata.
- **Missing/invalid metadata** degrades to on-demand mode with topic `"unknown"`.
- **On-demand** (both modes): a `fact_check_request` from participant X checks the
  *opponent's* last 30s. **Auto** (only when metadata says `auto`): each finalized
  segment considers that *speaker's* last 20s. Auto failures stay in logs — no error
  spam on the data channel.
- `session.py`, `pipeline/`, and `moderation/` import no LiveKit — the whole
  decision core is unit-testable (`pytest`, 111 tests, all external services
  mocked).
- Every check logs `match_id`, mode, claim hash, cache hit/miss, verdict, latency,
  and provider token usage (same `input_tokens`/`output_tokens` fields for both
  providers; Gemini adds `thought_tokens` when present) as JSON for cost tracking.
- Verification keeps the same envelope on both providers: 25s timeout, one retry,
  then `fact_check_error`.

The data-channel and room-metadata contracts are implemented verbatim from the spec —
field names must not change (debate-mobile parses them exactly).

## Cost notes

**Gemini (default):** the AI Studio free tier bills nothing — the constraint is
rate/daily quotas, not dollars. Watch for `gemini_quota` log events; grounded search
requests on flash also have their own free daily allotment. The rate limits below
keep a debate well inside them.

**Anthropic (when switched back):** per fact-check (typical):

- **Extraction** — Haiku 4.5 ($1/$5 per MTok): ~500 in / ~60 out ≈ **$0.001**
- **Verification** — Sonnet 4.6 ($3/$15 per MTok): ~2–6K in (search results) / ~300 out,
  plus web search at $10 per 1,000 searches (≤3 per claim) ≈ **$0.02–0.05 per claim**
- Up to 2 claims per check → worst case ~**$0.10 per on-demand request**

What keeps this bounded on either provider: the 10s per-user cooldown, one in-flight
check per room, auto mode's 20s per-speaker gap with drop-don't-queue, and the 72h
claim cache (repeated talking points across matches are free). The
`web_search_20250305` tool variant on the Anthropic path is pinned by the contract;
claude-sonnet-4-6 also supports the newer `web_search_20260209` (dynamic filtering)
if the contract is ever revised.

## Tests

```sh
pytest
```

111 tests, all external services mocked. Covers: normalization/hashing, cache
roundtrip + never-cache-unverifiable, metadata parsing incl. fallback, the Anthropic
strict-JSON re-ask-once-then-error flow, verification timeout/retry, model-404
no-substitution on both providers, Gemini structured-output extraction, grounded
sources (override, fallback, cap), the grounding-compatible re-ask, 429 quota
retry/fail-fast behavior, provider selection via `LLM_PROVIDER`, cooldown,
single-flight, empty-window and non-participant errors, auto-mode per-speaker gating
and inflight drops, and the full on-demand flow run through **both real provider
implementations** over scripted fakes.
