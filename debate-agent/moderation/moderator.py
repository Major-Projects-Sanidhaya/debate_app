"""Moderation orchestration for one match room.

LiveKit-free by design (the agent feeds it segments and JPEG bytes), so every
rule here — the per-speaker screening debounce, the dev test-phrase hook, the
video sample gate, and event posting — is unit-testable with fakes.

Every public method swallows its own failures: moderation must never delay or
break fact-checking, and never raise into the session.
"""

import time

import structlog

from moderation.classifier import VIDEO_CATEGORY_MAP, classify_frame, classify_text
from moderation.config import MAX_EXCERPT_CHARS, ModerationConfig

logger = structlog.get_logger(__name__)

TEST_CATEGORY = "test"


class IntervalGate:
    """Per-key rate gate: ready() returns True at most once per interval."""

    def __init__(self, interval: float, clock=time.time):
        self._interval = interval
        self._clock = clock
        self._last: "dict[str, float]" = {}

    def ready(self, key: str) -> bool:
        now = self._clock()
        last = self._last.get(key)
        if last is not None and now - last < self._interval:
            return False
        self._last[key] = now
        return True


class Moderator:
    def __init__(
        self,
        *,
        match_id: str,
        provider,
        internal_client,
        transcript,
        config: ModerationConfig,
        clock=time.time,
    ):
        self.match_id = match_id
        self._provider = provider
        self._internal = internal_client
        self._transcript = transcript
        self._config = config
        self._clock = clock
        self._text_gate = IntervalGate(config.text_screen_interval, clock)
        self._video_gate = IntervalGate(config.video_sample_interval, clock)

    # ---------------------------------------------------------- transcript

    async def on_final_segment(self, stance: str, text: str) -> None:
        """Screen a finalized STT segment. Called fire-and-forget by the agent."""
        try:
            phrase = self._config.test_phrase
            if phrase and phrase.lower() in text.lower():
                # Dev-only hook: no model call, straight to a severe event so
                # the end-to-end termination path can be exercised on demand.
                logger.warning("moderation_test_phrase_triggered", stance=stance)
                await self._post(
                    source="transcript",
                    stance=stance,
                    category=TEST_CATEGORY,
                    severity="severe",
                    excerpt=text[:MAX_EXCERPT_CHARS],
                )
                return

            if not self._text_gate.ready(stance):
                return

            window = self._transcript.window_text(
                stance, self._config.text_window_seconds, self._clock()
            )
            if not window.strip():
                return

            started = self._clock()
            screening = await classify_text(self._provider, window)
            if screening is None or not screening.is_violation:
                return
            await self._post(
                source="transcript",
                stance=stance,
                category=screening.category,
                severity=screening.severity,
                excerpt=window[-MAX_EXCERPT_CHARS:],
                latency=round(self._clock() - started, 2),
            )
        except Exception:
            logger.exception("moderation_segment_failed", stance=stance)

    # --------------------------------------------------------------- video

    def should_sample_video(self, stance: str) -> bool:
        """Gate before the (relatively costly) frame conversion."""
        return self._config.video_enabled and self._video_gate.ready(stance)

    async def on_video_frame(self, stance: str, jpeg_bytes: bytes) -> None:
        """Screen one in-memory frame. The bytes are never persisted anywhere."""
        try:
            started = self._clock()
            screening = await classify_frame(self._provider, jpeg_bytes)
            if screening is None or not screening.is_violation:
                return
            category = VIDEO_CATEGORY_MAP.get(screening.category, screening.category)
            await self._post(
                source="video",
                stance=stance,
                category=category,
                severity=screening.severity,
                excerpt="",  # contract: empty for video
                latency=round(self._clock() - started, 2),
            )
        except Exception:
            logger.exception("moderation_frame_failed", stance=stance)

    # ------------------------------------------------------------- posting

    async def _post(
        self,
        *,
        source: str,
        stance: str,
        category: str,
        severity: str,
        excerpt: str,
        latency: "float | None" = None,
    ) -> None:
        ts = int(self._clock())
        # Log metadata only — never frame bytes, and never transcript text.
        logger.warning(
            "moderation_violation",
            match_id=self.match_id,
            source=source,
            stance=stance,
            category=category,
            severity=severity,
            latency=latency,
            ts=ts,
        )
        await self._internal.post_event(
            match_id=self.match_id,
            source=source,
            stance=stance,
            category=category,
            severity=severity,
            excerpt=excerpt,
            ts=ts,
        )
