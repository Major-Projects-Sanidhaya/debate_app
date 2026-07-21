"""DebateSession rules: cooldown, single-flight, windows, auto gating, cache."""

import asyncio
import dataclasses

import pytest

from pipeline.cache import ClaimCache
from pipeline.models import PipelineError, Verdict
from session import DebateSession, stance_of
from transcript import RollingTranscript, Segment


def make_verdict(claim, verdict="false"):
    return Verdict(
        claim=claim, verdict=verdict, confidence="high", summary="Checked.", sources=[]
    )


class StubProvider:
    """Interface-shaped stub; the session only touches these two methods."""

    name = "stub"
    extraction_model = "stub-extract"
    verification_model = "stub-verify"

    def __init__(self, extract=None, verify=None):
        self.counts = {"extract": 0, "verify": 0}
        self._extract = extract
        self._verify = verify

    async def extract_claims(self, topic, window):
        self.counts["extract"] += 1
        if self._extract is not None:
            return await self._extract(topic, window)
        return ["Claim X"], {"input_tokens": 1, "output_tokens": 1}

    async def verify_claim(self, topic, claim):
        self.counts["verify"] += 1
        if self._verify is not None:
            return await self._verify(topic, claim)
        return make_verdict(claim), {}


def make_provider(*, claims=("Claim X",), verdicts=None):
    async def extract(topic, window):
        return list(claims), {"input_tokens": 1, "output_tokens": 1}

    async def verify(topic, claim):
        if verdicts and claim in verdicts:
            return verdicts[claim], {}
        return make_verdict(claim), {}

    return StubProvider(extract, verify)


@pytest.fixture
def published():
    return []


def build_session(meta, fake_redis, clock, published, provider, mode=None):
    if mode is not None:
        meta = dataclasses.replace(meta, fact_check_mode=mode)

    async def publish(message):
        published.append(message)

    return DebateSession(
        meta,
        provider=provider,
        cache=ClaimCache(fake_redis),
        transcript=RollingTranscript(),
        publish=publish,
        clock=clock,
    )


def seed(session, stance, text, ts):
    session.transcript.append(Segment(ts=ts, stance=stance, text=text))


# --------------------------------------------------------------- attribution


def test_stance_of_prefers_name_then_identity(meta):
    assert stance_of("anything", "pro", meta) == "pro"
    assert stance_of("uid-con", None, meta) == "con"
    assert stance_of("uid-pro", "not-a-stance", meta) == "pro"
    assert stance_of("stranger", None, meta) is None


# ------------------------------------------------------------ on-demand flow


async def test_on_demand_full_flow(meta, fake_redis, clock, published):
    provider = make_provider(claims=("Opponent claim",))
    session = build_session(meta, fake_redis, clock, published, provider)
    seed(session, "con", "the con speaker said something checkable", clock.now - 5)

    await session.handle_fact_check_request("uid-pro", "pro")

    assert [m["type"] for m in published] == ["fact_check_status", "verdict"]
    status, verdict = published
    assert status["status"] == "checking"
    assert status["target_stance"] == "con"  # the requester's opponent
    assert verdict["request_id"] == status["request_id"]
    assert verdict["match_id"] == "match-1"
    assert verdict["claim"] == "Opponent claim"
    assert verdict["verdict"] == "false"
    assert verdict["speaker_stance"] == "con"
    assert verdict["mode"] == "on_demand"
    assert isinstance(verdict["ts"], int)


async def test_empty_window_errors(meta, fake_redis, clock, published):
    provider = make_provider()
    session = build_session(meta, fake_redis, clock, published, provider)
    seed(session, "con", "old statement", clock.now - 45)  # outside the 30s window

    await session.handle_fact_check_request("uid-pro", "pro")

    assert [m["type"] for m in published] == ["fact_check_error"]
    assert "30 seconds" in published[0]["message"]


async def test_non_participant_rejected(meta, fake_redis, clock, published):
    session = build_session(meta, fake_redis, clock, published, make_provider())
    await session.handle_fact_check_request("random-listener", None)
    assert published[0]["type"] == "fact_check_error"


async def test_cooldown_enforced_per_user(meta, fake_redis, clock, published):
    provider = make_provider()
    session = build_session(meta, fake_redis, clock, published, provider)
    seed(session, "con", "something to check", clock.now - 1)

    await session.handle_fact_check_request("uid-pro", "pro")
    clock.advance(3)  # within the 10s cooldown
    seed(session, "con", "more talk", clock.now)
    await session.handle_fact_check_request("uid-pro", "pro")

    errors = [m for m in published if m["type"] == "fact_check_error"]
    assert len(errors) == 1 and "wait" in errors[0]["message"].lower()
    assert provider.counts["extract"] == 1  # second request never ran the pipeline

    clock.advance(11)  # cooldown expired
    await session.handle_fact_check_request("uid-pro", "pro")
    assert provider.counts["extract"] == 2


async def test_cooldown_is_per_user_not_global(meta, fake_redis, clock, published):
    provider = make_provider()
    session = build_session(meta, fake_redis, clock, published, provider)
    seed(session, "con", "con talk", clock.now - 1)
    seed(session, "pro", "pro talk", clock.now - 1)

    await session.handle_fact_check_request("uid-pro", "pro")
    clock.advance(2)
    await session.handle_fact_check_request("uid-con", "con")  # other user: allowed

    assert provider.counts["extract"] == 2
    assert not [m for m in published if m["type"] == "fact_check_error"]


async def test_single_flight_rejects_concurrent_request(meta, fake_redis, clock, published):
    release = asyncio.Event()
    verify_calls = []

    async def extract(topic, window):
        return ["Slow claim"], {}

    async def slow_verify(topic, claim):
        verify_calls.append(claim)
        await release.wait()
        return make_verdict(claim), {}

    provider = StubProvider(extract, slow_verify)
    session = build_session(meta, fake_redis, clock, published, provider)
    seed(session, "con", "con talk", clock.now - 1)
    seed(session, "pro", "pro talk", clock.now - 1)

    first = asyncio.create_task(session.handle_fact_check_request("uid-pro", "pro"))
    while not verify_calls:  # let the first request reach verification
        await asyncio.sleep(0)

    await session.handle_fact_check_request("uid-con", "con")  # while in flight
    errors = [m for m in published if m["type"] == "fact_check_error"]
    assert len(errors) == 1 and "already running" in errors[0]["message"]

    release.set()
    await first
    assert [m["type"] for m in published if m["type"] == "verdict"] == ["verdict"]


async def test_pipeline_error_becomes_fact_check_error(meta, fake_redis, clock, published):
    async def failing_extract(topic, window):
        raise PipelineError("The fact-checker is unavailable right now.")

    provider = StubProvider(failing_extract, None)
    session = build_session(meta, fake_redis, clock, published, provider)
    seed(session, "con", "con talk", clock.now - 1)

    await session.handle_fact_check_request("uid-pro", "pro")

    types = [m["type"] for m in published]
    assert types == ["fact_check_status", "fact_check_error"]
    assert published[1]["message"] == "The fact-checker is unavailable right now."


# ------------------------------------------------------------------- caching


async def test_cache_hit_skips_verification(meta, fake_redis, clock, published):
    provider = make_provider(claims=("Cached claim",))
    session = build_session(meta, fake_redis, clock, published, provider)
    await ClaimCache(fake_redis).put("Cached claim", make_verdict("Cached claim"))
    seed(session, "con", "says the cached thing", clock.now - 1)

    await session.handle_fact_check_request("uid-pro", "pro")

    assert provider.counts["verify"] == 0  # served from cache
    verdict = [m for m in published if m["type"] == "verdict"][0]
    assert verdict["claim"] == "Cached claim"
    assert verdict["request_id"]  # fresh request id
    assert verdict["ts"] == int(clock.now)


async def test_unverifiable_not_cached_via_session(meta, fake_redis, clock, published):
    provider = make_provider(
        claims=("Fuzzy claim",),
        verdicts={"Fuzzy claim": make_verdict("Fuzzy claim", verdict="unverifiable")},
    )
    session = build_session(meta, fake_redis, clock, published, provider)
    seed(session, "con", "vague assertion", clock.now - 1)

    await session.handle_fact_check_request("uid-pro", "pro")

    verdict = [m for m in published if m["type"] == "verdict"][0]
    assert verdict["verdict"] == "unverifiable"
    assert await ClaimCache(fake_redis).get("Fuzzy claim") is None


# ----------------------------------------------------------------- auto mode


async def test_auto_mode_checks_on_segment(meta, fake_redis, clock, published):
    provider = make_provider(claims=("Auto claim",))
    session = build_session(meta, fake_redis, clock, published, provider, mode="auto")

    await session.on_final_segment("con", "a checkable statement")

    assert provider.counts["extract"] == 1
    verdict = [m for m in published if m["type"] == "verdict"][0]
    assert verdict["mode"] == "auto"
    assert verdict["speaker_stance"] == "con"  # the speaker themselves
    # Auto mode never emits fact_check_status
    assert not [m for m in published if m["type"] == "fact_check_status"]


async def test_auto_mode_20s_per_speaker_gate(meta, fake_redis, clock, published):
    provider = make_provider(claims=("Auto claim",))
    session = build_session(meta, fake_redis, clock, published, provider, mode="auto")

    await session.on_final_segment("con", "first statement")
    clock.advance(5)
    await session.on_final_segment("con", "second statement too soon")
    assert provider.counts["extract"] == 1  # gated

    clock.advance(3)
    await session.on_final_segment("pro", "other speaker is independent")
    assert provider.counts["extract"] == 2  # per-speaker, not global

    clock.advance(21)
    await session.on_final_segment("con", "after the gap")
    assert provider.counts["extract"] == 3


async def test_auto_mode_drops_when_inflight(meta, fake_redis, clock, published):
    release = asyncio.Event()
    verify_calls = []

    async def extract(topic, window):
        return ["Busy claim"], {}

    async def slow_verify(topic, claim):
        verify_calls.append(claim)
        await release.wait()
        return make_verdict(claim), {}

    provider = StubProvider(extract, slow_verify)
    session = build_session(meta, fake_redis, clock, published, provider, mode="auto")

    first = asyncio.create_task(session.on_final_segment("con", "long statement"))
    while not verify_calls:
        await asyncio.sleep(0)

    await session.on_final_segment("pro", "dropped, not queued")  # other speaker, inflight
    assert len(verify_calls) == 1

    release.set()
    await first
    await asyncio.sleep(0)
    assert len(verify_calls) == 1  # the dropped check never ran later


async def test_auto_failures_stay_silent(meta, fake_redis, clock, published):
    async def failing_extract(topic, window):
        raise PipelineError("boom")

    provider = StubProvider(failing_extract, None)
    session = build_session(meta, fake_redis, clock, published, provider, mode="auto")

    await session.on_final_segment("con", "statement")

    assert published == []  # no error spam on the data channel in auto mode


async def test_on_demand_mode_never_auto_checks(meta, fake_redis, clock, published):
    provider = make_provider()
    session = build_session(meta, fake_redis, clock, published, provider)  # on_demand

    await session.on_final_segment("con", "a statement")

    assert provider.counts["extract"] == 0
    assert session.transcript.segments  # still recorded for later requests


# ----------------------------------- full flow through the real provider code


GOOD_VERDICT_JSON = (
    '{"verdict":"false","confidence":"high",'
    '"summary":"FBI data shows violent crime fell since 1991.",'
    '"sources":[{"title":"Model Source","url":"https://model.example"}]}'
)


@pytest.fixture(params=["anthropic", "gemini"])
def real_provider(request):
    """Both provider implementations over scripted fakes: one extraction call,
    then one verification call."""
    if request.param == "anthropic":
        from pipeline.providers.anthropic_provider import AnthropicProvider
        from tests.conftest import FakeAnthropic, text_response

        client = FakeAnthropic(
            [text_response('{"claims": ["Opponent claim"]}'), text_response(GOOD_VERDICT_JSON)]
        )
        return AnthropicProvider(client=client)

    from pipeline.providers.gemini_provider import GeminiProvider
    from tests.conftest import FakeGeminiClient, gemini_chunk, gemini_response

    client = FakeGeminiClient(
        [
            gemini_response('{"claims": ["Opponent claim"]}'),
            gemini_response(
                GOOD_VERDICT_JSON,
                grounding=[gemini_chunk("Grounded Source", "https://grounded.example")],
            ),
        ]
    )
    return GeminiProvider(client=client)


async def test_on_demand_flow_through_real_provider(
    meta, fake_redis, clock, published, real_provider
):
    session = build_session(meta, fake_redis, clock, published, real_provider)
    seed(session, "con", "the con speaker said something checkable", clock.now - 5)

    await session.handle_fact_check_request("uid-pro", "pro")

    assert [m["type"] for m in published] == ["fact_check_status", "verdict"]
    verdict = published[1]
    assert verdict["claim"] == "Opponent claim"
    assert verdict["verdict"] == "false"
    assert verdict["sources"], "sources must be populated"
    if real_provider.name == "gemini":
        # Grounding metadata beats the model's self-reported sources.
        assert verdict["sources"] == [
            {"title": "Grounded Source", "url": "https://grounded.example"}
        ]
