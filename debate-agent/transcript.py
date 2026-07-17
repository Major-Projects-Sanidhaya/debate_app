"""Rolling in-memory transcript with a Redis mirror (moderation evidence, 24h TTL)."""

import json
import time
from dataclasses import asdict, dataclass

import structlog

logger = structlog.get_logger(__name__)

MIRROR_TTL_SECONDS = 24 * 3600


@dataclass
class Segment:
    ts: float
    stance: str  # "pro" | "con"
    text: str


class RollingTranscript:
    def __init__(self):
        self.segments: "list[Segment]" = []

    def append(self, segment: Segment) -> None:
        self.segments.append(segment)

    def window_text(self, stance: str, seconds: float, now: "float | None" = None) -> str:
        now = time.time() if now is None else now
        cutoff = now - seconds
        return " ".join(
            s.text for s in self.segments if s.stance == stance and s.ts >= cutoff
        ).strip()


class TranscriptMirror:
    """Append-only list at transcript:{match_id}, refreshed to a 24h TTL."""

    def __init__(self, redis, match_id: str):
        self._redis = redis
        self.key = f"transcript:{match_id}"

    async def append(self, segment: Segment) -> None:
        try:
            await self._redis.rpush(self.key, json.dumps(asdict(segment)))
            await self._redis.expire(self.key, MIRROR_TTL_SECONDS)
        except Exception as exc:
            # Mirroring is evidence-keeping, never in the hot path's way.
            logger.warning("transcript_mirror_failed", error=repr(exc))
