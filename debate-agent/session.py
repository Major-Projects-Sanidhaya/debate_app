"""DebateSession: fact-check orchestration and rate limiting for one match room.

Deliberately LiveKit-free: the agent wires room events in and passes an async
`publish` callback out, so every rule here (cooldowns, single-flight, windows,
auto gating) is unit-testable with fakes.
"""

import time
import uuid

import structlog

from pipeline.cache import ClaimCache
from pipeline.models import PipelineError, RoomMeta
from pipeline.normalize import claim_hash
from pipeline.providers.base import LLMProvider
from transcript import RollingTranscript, Segment, TranscriptMirror

logger = structlog.get_logger(__name__)

ON_DEMAND_WINDOW_SECONDS = 30.0
AUTO_WINDOW_SECONDS = 20.0
REQUEST_COOLDOWN_SECONDS = 10.0
AUTO_PER_SPEAKER_GAP_SECONDS = 20.0

STANCES = ("pro", "con")


def opposite(stance: str) -> str:
    return "con" if stance == "pro" else "pro"


def stance_of(identity: str, name: "str | None", meta: RoomMeta) -> "str | None":
    """Participant name is "pro"/"con"; fall back to matching identity to metadata."""
    if name in STANCES:
        return name
    if identity and identity == meta.user_pro:
        return "pro"
    if identity and identity == meta.user_con:
        return "con"
    return None


class DebateSession:
    def __init__(
        self,
        meta: RoomMeta,
        *,
        provider: LLMProvider,
        cache: ClaimCache,
        transcript: RollingTranscript,
        publish,  # async (dict) -> None; broadcast on the fact_check topic
        mirror: "TranscriptMirror | None" = None,
        clock=time.time,
    ):
        self.meta = meta
        self._provider = provider
        self._cache = cache
        self.transcript = transcript
        self._publish = publish
        self._mirror = mirror
        self._clock = clock

        self._inflight = False
        self._last_request_ts: "dict[str, float]" = {}  # per requester identity
        self._last_auto_ts: "dict[str, float]" = {}  # per speaker stance

    # ---------------------------------------------------------------- intake

    async def on_final_segment(self, stance: str, text: str, ts: "float | None" = None) -> None:
        """A finalized STT segment: record it, mirror it, maybe auto-check it."""
        segment = Segment(ts=self._clock() if ts is None else ts, stance=stance, text=text)
        self.transcript.append(segment)
        if self._mirror is not None:
            await self._mirror.append(segment)
        if self.meta.fact_check_mode == "auto":
            await self._maybe_auto_check(stance)

    async def handle_fact_check_request(self, identity: str, name: "str | None") -> None:
        """On-demand flow — available in both modes."""
        request_id = str(uuid.uuid4())
        now = self._clock()

        requester_stance = stance_of(identity, name, self.meta)
        if requester_stance is None:
            await self._error(request_id, "Only debate participants can request fact-checks.")
            return

        last = self._last_request_ts.get(identity)
        if last is not None and now - last < REQUEST_COOLDOWN_SECONDS:
            await self._error(
                request_id, "Please wait a few seconds between fact-check requests."
            )
            return

        if self._inflight:
            await self._error(request_id, "A fact-check is already running for this debate.")
            return

        target_stance = opposite(requester_stance)
        window = self.transcript.window_text(target_stance, ON_DEMAND_WINDOW_SECONDS, now)
        if not window:
            await self._error(
                request_id, "Your opponent hasn't said anything to check in the last 30 seconds."
            )
            return

        self._last_request_ts[identity] = now
        self._inflight = True
        try:
            await self._publish(
                {
                    "type": "fact_check_status",
                    "request_id": request_id,
                    "status": "checking",
                    "target_stance": target_stance,
                }
            )
            await self._run_pipeline(
                request_id, target_stance, window, mode="on_demand", emit_errors=True
            )
        finally:
            self._inflight = False

    # ------------------------------------------------------------- auto mode

    async def _maybe_auto_check(self, stance: str) -> None:
        now = self._clock()
        if self._inflight:
            logger.info("auto_check_dropped", match_id=self.meta.match_id, reason="inflight")
            return
        last = self._last_auto_ts.get(stance)
        if last is not None and now - last < AUTO_PER_SPEAKER_GAP_SECONDS:
            return
        window = self.transcript.window_text(stance, AUTO_WINDOW_SECONDS, now)
        if not window:
            return

        self._last_auto_ts[stance] = now
        self._inflight = True
        request_id = str(uuid.uuid4())
        try:
            # Auto checks are ambient: failures and empty extractions stay in
            # the logs instead of interrupting the debate with error messages.
            await self._run_pipeline(request_id, stance, window, mode="auto", emit_errors=False)
        finally:
            self._inflight = False

    # -------------------------------------------------------------- pipeline

    async def _run_pipeline(
        self, request_id: str, speaker_stance: str, window: str, *, mode: str, emit_errors: bool
    ) -> None:
        log = logger.bind(match_id=self.meta.match_id, mode=mode, request_id=request_id)
        started = self._clock()

        try:
            claims, usage = await self._provider.extract_claims(self.meta.topic, window)
        except PipelineError as exc:
            log.warning("extraction_failed", error=exc.user_message)
            if emit_errors:
                await self._error(request_id, exc.user_message)
            return
        log.info("claims_extracted", count=len(claims), usage=usage)

        if not claims:
            if emit_errors:
                await self._error(
                    request_id, "No checkable factual claims found in the recent audio."
                )
            return

        for claim in claims:
            digest = claim_hash(claim)
            claim_log = log.bind(claim_hash=digest)
            cached = await self._cache.get(claim)
            if cached is not None:
                await self._publish(
                    self._verdict_message(request_id, cached, speaker_stance, mode)
                )
                claim_log.info(
                    "fact_check_complete",
                    cache="hit",
                    verdict=cached.get("verdict"),
                    latency_s=round(self._clock() - started, 2),
                )
                continue

            try:
                verdict, vusage = await self._provider.verify_claim(self.meta.topic, claim)
            except PipelineError as exc:
                claim_log.warning("verification_failed", error=exc.user_message)
                if emit_errors:
                    await self._error(request_id, exc.user_message)
                continue

            await self._cache.put(claim, verdict)
            await self._publish(
                self._verdict_message(request_id, verdict.core_dict(), speaker_stance, mode)
            )
            claim_log.info(
                "fact_check_complete",
                cache="miss",
                verdict=verdict.verdict,
                confidence=verdict.confidence,
                latency_s=round(self._clock() - started, 2),
                usage=vusage,
            )

    # -------------------------------------------------------------- messages

    def _verdict_message(
        self, request_id: str, core: dict, speaker_stance: str, mode: str
    ) -> dict:
        return {
            "type": "verdict",
            "request_id": request_id,
            "match_id": self.meta.match_id,
            "claim": core["claim"],
            "verdict": core["verdict"],
            "confidence": core["confidence"],
            "summary": core["summary"],
            "sources": core.get("sources", []),
            "speaker_stance": speaker_stance,
            "mode": mode,
            "ts": int(self._clock()),
        }

    async def _error(self, request_id: str, message: str) -> None:
        await self._publish(
            {"type": "fact_check_error", "request_id": request_id, "message": message}
        )
