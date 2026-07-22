"""Moderation: classifier, debounce, test hook, internal client, frame path.

Everything external is mocked — no network, no model, no LiveKit.
"""

import asyncio
from types import SimpleNamespace

import pytest

from moderation.classifier import Screening, classify_frame, classify_text
from moderation.config import ModerationConfig
from moderation.frames import encode_rgb_to_jpeg
from moderation.internal_client import InternalModerationClient
from moderation.moderator import IntervalGate, Moderator
from pipeline.models import PipelineError
from transcript import RollingTranscript, Segment


class FakeProvider:
    """Scripted complete_json: dicts, exceptions, or callables."""

    name = "fake"
    extraction_model = "fake-model"
    verification_model = "fake-model"

    def __init__(self, script):
        self.script = list(script)
        self.calls: list = []

    async def complete_json(self, system, user, *, image_bytes=None, image_mime="image/jpeg"):
        self.calls.append(
            {"system": system, "user": user, "image_bytes": image_bytes, "image_mime": image_mime}
        )
        if not self.script:
            raise AssertionError("FakeProvider ran out of scripted responses")
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item, {"input_tokens": 10, "output_tokens": 5}


class FakeInternalClient:
    def __init__(self, result=True):
        self.events: list = []
        self._result = result

    async def post_event(self, **kwargs):
        self.events.append(kwargs)
        return self._result


class FakeHttp:
    """Scripted httpx-ish client: ints are status codes, exceptions raise."""

    def __init__(self, script):
        self.script = list(script)
        self.calls: list = []

    async def post(self, url, json=None, headers=None):
        self.calls.append({"url": url, "json": json, "headers": headers})
        item = self.script.pop(0) if self.script else 204
        if isinstance(item, Exception):
            raise item
        return SimpleNamespace(status_code=item)


class FakeClock:
    def __init__(self, now=1000.0):
        self.now = now

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


def clean(category="none", severity="none"):
    return {"category": category, "severity": severity}


def build_moderator(provider, internal, *, clock=None, config=None, transcript=None):
    clock = clock or FakeClock()
    transcript = transcript if transcript is not None else RollingTranscript()
    config = config or ModerationConfig(internal_api_key="k", video_sample_interval=10.0)
    return (
        Moderator(
            match_id="match-1",
            provider=provider,
            internal_client=internal,
            transcript=transcript,
            config=config,
            clock=clock,
        ),
        clock,
        transcript,
    )


def seed(transcript, stance, text, ts):
    transcript.append(Segment(ts=ts, stance=stance, text=text))


# ------------------------------------------------------------------ classifier


async def test_classify_text_parses_violation():
    provider = FakeProvider([clean("harassment_hate", "severe")])
    screening = await classify_text(provider, "some window")
    assert screening == Screening(category="harassment_hate", severity="severe")
    assert screening.is_violation


async def test_classify_text_clean_is_not_a_violation():
    provider = FakeProvider([clean()])
    screening = await classify_text(provider, "heated but fine")
    assert screening.is_violation is False


async def test_classify_malformed_reasks_once_then_succeeds():
    provider = FakeProvider([PipelineError("unreadable"), clean("self_harm", "medium")])
    screening = await classify_text(provider, "window")
    assert screening.category == "self_harm"
    assert len(provider.calls) == 2
    assert "ONLY the JSON object" in provider.calls[1]["user"]  # corrective re-ask


async def test_classify_malformed_twice_drops():
    provider = FakeProvider([PipelineError("bad"), PipelineError("bad again")])
    assert await classify_text(provider, "window") is None
    assert len(provider.calls) == 2  # exactly one re-ask, then drop


async def test_classify_rejects_off_contract_category():
    provider = FakeProvider([clean("spicy_takes", "medium"), clean("harassment_hate", "medium")])
    screening = await classify_text(provider, "window")
    assert screening.category == "harassment_hate"  # recovered on the re-ask


async def test_classify_rejects_incoherent_pair():
    provider = FakeProvider([clean("harassment_hate", "none"), clean()])
    screening = await classify_text(provider, "window")
    assert screening.is_violation is False


async def test_classify_frame_sends_image_bytes():
    provider = FakeProvider([clean("sexual_content", "severe")])
    screening = await classify_frame(provider, b"\xff\xd8jpegbytes")
    assert screening.category == "sexual_content"
    assert provider.calls[0]["image_bytes"] == b"\xff\xd8jpegbytes"
    assert provider.calls[0]["image_mime"] == "image/jpeg"


# ----------------------------------------------------------- transcript screening


async def test_severe_transcript_event_body():
    provider = FakeProvider([clean("violence_threat", "severe")])
    internal = FakeInternalClient()
    moderator, clock, transcript = build_moderator(provider, internal)
    seed(transcript, "con", "the offending statement", clock.now - 5)

    await moderator.on_final_segment("con", "the offending statement")

    assert len(internal.events) == 1
    event = internal.events[0]
    assert event == {
        "match_id": "match-1",
        "source": "transcript",
        "stance": "con",
        "category": "violence_threat",
        "severity": "severe",
        "excerpt": "the offending statement",
        "ts": int(clock.now),
    }


async def test_medium_transcript_event_posts():
    provider = FakeProvider([clean("harassment_hate", "medium")])
    internal = FakeInternalClient()
    moderator, clock, transcript = build_moderator(provider, internal)
    seed(transcript, "pro", "borderline content", clock.now - 1)

    await moderator.on_final_segment("pro", "borderline content")

    assert [e["severity"] for e in internal.events] == ["medium"]


async def test_profanity_only_produces_no_event():
    # The classifier says clean (profanity/heat is not a violation) -> no post.
    provider = FakeProvider([clean()])
    internal = FakeInternalClient()
    moderator, clock, transcript = build_moderator(provider, internal)
    seed(transcript, "pro", "that policy is absolute garbage and you're damn wrong", clock.now)

    await moderator.on_final_segment("pro", "that policy is absolute garbage and you're damn wrong")

    assert internal.events == []


async def test_excerpt_capped_at_300_chars():
    provider = FakeProvider([clean("harassment_hate", "severe")])
    internal = FakeInternalClient()
    moderator, clock, transcript = build_moderator(provider, internal)
    seed(transcript, "con", "x" * 900, clock.now)

    await moderator.on_final_segment("con", "x" * 900)

    assert len(internal.events[0]["excerpt"]) == 300


async def test_screening_debounced_to_15s_per_speaker():
    provider = FakeProvider([clean(), clean(), clean()])
    internal = FakeInternalClient()
    moderator, clock, transcript = build_moderator(provider, internal)
    seed(transcript, "pro", "first", clock.now)

    await moderator.on_final_segment("pro", "first")
    assert len(provider.calls) == 1

    clock.advance(5)
    seed(transcript, "pro", "second", clock.now)
    await moderator.on_final_segment("pro", "second")
    assert len(provider.calls) == 1  # gated

    clock.advance(11)  # 16s since the first screening
    seed(transcript, "pro", "third", clock.now)
    await moderator.on_final_segment("pro", "third")
    assert len(provider.calls) == 2


async def test_debounce_is_per_speaker():
    provider = FakeProvider([clean(), clean()])
    internal = FakeInternalClient()
    moderator, clock, transcript = build_moderator(provider, internal)
    seed(transcript, "pro", "pro talk", clock.now)
    seed(transcript, "con", "con talk", clock.now)

    await moderator.on_final_segment("pro", "pro talk")
    await moderator.on_final_segment("con", "con talk")

    assert len(provider.calls) == 2  # independent gates


async def test_empty_window_skips_model_call():
    provider = FakeProvider([])
    internal = FakeInternalClient()
    moderator, clock, _ = build_moderator(provider, internal)

    await moderator.on_final_segment("pro", "spoken but nothing in the window")

    assert provider.calls == []
    assert internal.events == []


async def test_classifier_failure_never_raises_and_posts_nothing():
    provider = FakeProvider([PipelineError("x"), PipelineError("x")])
    internal = FakeInternalClient()
    moderator, clock, transcript = build_moderator(provider, internal)
    seed(transcript, "pro", "text", clock.now)

    await moderator.on_final_segment("pro", "text")  # must not raise

    assert internal.events == []


async def test_internal_failure_never_raises():
    provider = FakeProvider([clean("harassment_hate", "severe")])

    class ExplodingClient:
        async def post_event(self, **kwargs):
            raise RuntimeError("network down")

    moderator, clock, transcript = build_moderator(provider, ExplodingClient())
    seed(transcript, "pro", "bad stuff", clock.now)

    await moderator.on_final_segment("pro", "bad stuff")  # swallowed


# --------------------------------------------------------------- test phrase hook


async def test_test_phrase_fires_without_model_call():
    provider = FakeProvider([])  # any model call would blow up
    internal = FakeInternalClient()
    config = ModerationConfig(internal_api_key="k", test_phrase="banana protocol")
    moderator, clock, _ = build_moderator(provider, internal, config=config)

    await moderator.on_final_segment("pro", "I now invoke the BANANA PROTOCOL, friend")

    assert provider.calls == []  # no model involved
    assert len(internal.events) == 1
    event = internal.events[0]
    assert event["category"] == "test"
    assert event["severity"] == "severe"
    assert event["source"] == "transcript"
    assert event["stance"] == "pro"


async def test_test_phrase_bypasses_debounce():
    provider = FakeProvider([])
    internal = FakeInternalClient()
    config = ModerationConfig(internal_api_key="k", test_phrase="trigger")
    moderator, clock, _ = build_moderator(provider, internal, config=config)

    await moderator.on_final_segment("pro", "trigger one")
    await moderator.on_final_segment("pro", "trigger two")

    assert len(internal.events) == 2  # immediate every time, no 15s gate


async def test_no_test_phrase_configured_means_normal_path():
    provider = FakeProvider([clean()])
    internal = FakeInternalClient()
    moderator, clock, transcript = build_moderator(provider, internal)  # test_phrase=""
    seed(transcript, "pro", "trigger", clock.now)

    await moderator.on_final_segment("pro", "trigger")

    assert len(provider.calls) == 1  # normal classification, no synthetic event
    assert internal.events == []


# ---------------------------------------------------------------- video moderation


async def test_frame_path_posts_event_and_persists_nothing(tmp_path, monkeypatch):
    provider = FakeProvider([clean("sexual_content", "severe")])
    internal = FakeInternalClient()
    moderator, clock, _ = build_moderator(provider, internal)

    monkeypatch.chdir(tmp_path)  # any stray file write would land here
    before = set(tmp_path.iterdir())

    await moderator.on_video_frame("con", b"\xff\xd8fake-jpeg-bytes")

    assert len(internal.events) == 1
    event = internal.events[0]
    assert event["source"] == "video"
    assert event["excerpt"] == ""  # contract: empty for video
    assert event["category"] == "sexual_content"
    assert event["stance"] == "con"
    assert set(tmp_path.iterdir()) == before  # nothing written to disk


async def test_violence_gore_maps_to_violence_threat():
    provider = FakeProvider([clean("violence_gore", "severe")])
    internal = FakeInternalClient()
    moderator, _, _ = build_moderator(provider, internal)

    await moderator.on_video_frame("pro", b"jpeg")

    assert internal.events[0]["category"] == "violence_threat"


async def test_clean_frame_posts_nothing():
    provider = FakeProvider([clean()])
    internal = FakeInternalClient()
    moderator, _, _ = build_moderator(provider, internal)

    await moderator.on_video_frame("pro", b"jpeg")

    assert internal.events == []


async def test_video_sample_interval_gating():
    provider = FakeProvider([])
    internal = FakeInternalClient()
    config = ModerationConfig(internal_api_key="k", video_sample_interval=10.0)
    moderator, clock, _ = build_moderator(provider, internal, config=config)

    assert moderator.should_sample_video("pro") is True
    assert moderator.should_sample_video("pro") is False  # immediately after
    clock.advance(4)
    assert moderator.should_sample_video("pro") is False
    clock.advance(7)  # 11s since the last sample
    assert moderator.should_sample_video("pro") is True
    # per-speaker, like the transcript gate
    assert moderator.should_sample_video("con") is True


async def test_video_disabled_never_samples():
    config = ModerationConfig(internal_api_key="k", video_enabled=False)
    moderator, _, _ = build_moderator(FakeProvider([]), FakeInternalClient(), config=config)
    assert moderator.should_sample_video("pro") is False


def test_frame_encoder_downscales_in_memory():
    # 1200x600 raw RGB -> JPEG with long edge <= 512, no filesystem involvement.
    raw = bytes([120, 130, 140]) * (1200 * 600)
    jpeg = encode_rgb_to_jpeg(1200, 600, raw)
    assert jpeg[:2] == b"\xff\xd8"  # JPEG SOI marker

    from io import BytesIO

    from PIL import Image

    decoded = Image.open(BytesIO(jpeg))
    assert max(decoded.size) <= 512
    assert decoded.size == (512, 256)  # aspect ratio preserved


def test_frame_encoder_leaves_small_frames_unscaled():
    raw = bytes([10, 20, 30]) * (320 * 240)
    jpeg = encode_rgb_to_jpeg(320, 240, raw)

    from io import BytesIO

    from PIL import Image

    assert Image.open(BytesIO(jpeg)).size == (320, 240)


# ---------------------------------------------------------------- interval gate


def test_interval_gate_basics():
    clock = FakeClock()
    gate = IntervalGate(15.0, clock)
    assert gate.ready("a") is True
    assert gate.ready("a") is False
    assert gate.ready("b") is True  # independent keys
    clock.advance(15.1)
    assert gate.ready("a") is True


# -------------------------------------------------------------- internal client


async def test_internal_client_sends_contract_body_and_header():
    http = FakeHttp([204])
    client = InternalModerationClient("http://api.test", "secret-key", http=http)

    ok = await client.post_event(
        match_id="m1",
        source="transcript",
        stance="pro",
        category="harassment_hate",
        severity="severe",
        excerpt="bad words",
        ts=1700000000,
    )

    assert ok is True
    call = http.calls[0]
    assert call["url"] == "http://api.test/internal/moderation/events"
    assert call["headers"]["X-Internal-Key"] == "secret-key"
    assert call["json"] == {
        "match_id": "m1",
        "source": "transcript",
        "stance": "pro",
        "category": "harassment_hate",
        "severity": "severe",
        "excerpt": "bad words",
        "ts": 1700000000,
    }


async def test_internal_client_retries_once_then_succeeds():
    http = FakeHttp([500, 204])
    client = InternalModerationClient("http://api.test", "k", http=http)

    assert await client.post_event(
        match_id="m", source="video", stance="con", category="sexual_content",
        severity="medium", excerpt="", ts=1,
    ) is True
    assert len(http.calls) == 2


async def test_internal_client_gives_up_after_one_retry():
    http = FakeHttp([500, 500])
    client = InternalModerationClient("http://api.test", "k", http=http)

    assert await client.post_event(
        match_id="m", source="video", stance="con", category="sexual_content",
        severity="medium", excerpt="", ts=1,
    ) is False
    assert len(http.calls) == 2  # original + exactly one retry, then dropped


async def test_internal_client_swallows_transport_errors():
    http = FakeHttp([RuntimeError("connection refused"), RuntimeError("still down")])
    client = InternalModerationClient("http://api.test", "k", http=http)

    # Must not raise — moderation failures never reach the session.
    assert await client.post_event(
        match_id="m", source="transcript", stance="pro", category="test",
        severity="severe", excerpt="x", ts=1,
    ) is False


async def test_internal_client_url_normalizes_trailing_slash():
    http = FakeHttp([204])
    client = InternalModerationClient("http://api.test/", "k", http=http)
    await client.post_event(
        match_id="m", source="video", stance="pro", category="none",
        severity="medium", excerpt="", ts=1,
    )
    assert http.calls[0]["url"] == "http://api.test/internal/moderation/events"


# ------------------------------------------------------- config from environment


def test_config_from_env_defaults(monkeypatch):
    for var in (
        "INTERNAL_API_URL", "INTERNAL_API_KEY", "MODERATION_TEST_PHRASE",
        "VIDEO_SAMPLE_INTERVAL", "VIDEO_MODERATION_ENABLED",
    ):
        monkeypatch.delenv(var, raising=False)
    config = ModerationConfig.from_env()
    assert config.internal_api_url == "http://localhost:8000"
    assert config.test_phrase == ""
    assert config.video_sample_interval == 10.0
    assert config.video_enabled is True


def test_config_from_env_overrides(monkeypatch):
    monkeypatch.setenv("INTERNAL_API_URL", "http://api:9000")
    monkeypatch.setenv("INTERNAL_API_KEY", "sekret")
    monkeypatch.setenv("MODERATION_TEST_PHRASE", "  banana protocol  ")
    monkeypatch.setenv("VIDEO_SAMPLE_INTERVAL", "3.5")
    monkeypatch.setenv("VIDEO_MODERATION_ENABLED", "false")
    config = ModerationConfig.from_env()
    assert config.internal_api_url == "http://api:9000"
    assert config.internal_api_key == "sekret"
    assert config.test_phrase == "banana protocol"
    assert config.video_sample_interval == 3.5
    assert config.video_enabled is False


async def test_moderation_does_not_block_on_slow_classification():
    """Sanity: on_final_segment is awaited by a task, so a slow model can't
    stall the caller — the agent fires it and moves on."""
    started = asyncio.Event()
    release = asyncio.Event()

    class SlowProvider(FakeProvider):
        async def complete_json(self, system, user, **kwargs):
            started.set()
            await release.wait()
            return clean(), {}

    provider = SlowProvider([])
    internal = FakeInternalClient()
    moderator, clock, transcript = build_moderator(provider, internal)
    seed(transcript, "pro", "text", clock.now)

    task = asyncio.create_task(moderator.on_final_segment("pro", "text"))
    await asyncio.wait_for(started.wait(), timeout=1)
    assert not task.done()  # caller would be free here
    release.set()
    await asyncio.wait_for(task, timeout=1)
