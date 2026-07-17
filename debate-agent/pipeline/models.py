"""Shared pipeline types and room-metadata parsing (no LiveKit imports)."""

import json
from dataclasses import dataclass, field

import structlog

logger = structlog.get_logger(__name__)

VERDICTS = ("true", "false", "misleading", "unverifiable")
CONFIDENCES = ("high", "medium", "low")
MODES = ("on_demand", "auto")


class PipelineError(Exception):
    """Raised when a check cannot complete; carries a user-safe message."""

    def __init__(self, user_message: str):
        super().__init__(user_message)
        self.user_message = user_message


@dataclass
class Verdict:
    claim: str
    verdict: str  # true | false | misleading | unverifiable
    confidence: str  # high | medium | low
    summary: str
    sources: list = field(default_factory=list)  # [{"title": ..., "url": ...}]

    def core_dict(self) -> dict:
        return {
            "claim": self.claim,
            "verdict": self.verdict,
            "confidence": self.confidence,
            "summary": self.summary,
            "sources": self.sources,
        }


@dataclass
class RoomMeta:
    match_id: str
    topic_id: "int | None"
    topic: str
    fact_check_mode: str
    user_pro: "str | None"
    user_con: "str | None"


def _fallback_meta(room_name: str) -> RoomMeta:
    match_id = room_name.removeprefix("match_") or room_name
    return RoomMeta(
        match_id=match_id,
        topic_id=None,
        topic="unknown",
        fact_check_mode="on_demand",
        user_pro=None,
        user_con=None,
    )


def parse_room_metadata(raw: "str | None", *, room_name: str) -> RoomMeta:
    """Parse the ROOM METADATA CONTRACT set by debate-api.

    Missing or invalid metadata degrades to on_demand mode with topic
    "unknown" (warning logged) — the agent still serves manual checks.
    """
    if not raw:
        logger.warning("room_metadata_missing", room=room_name)
        return _fallback_meta(room_name)
    try:
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError("metadata is not an object")
        mode = data["fact_check_mode"]
        if mode not in MODES:
            raise ValueError(f"invalid fact_check_mode: {mode!r}")
        return RoomMeta(
            match_id=str(data["match_id"]),
            topic_id=int(data["topic_id"]),
            topic=str(data["topic"]),
            fact_check_mode=mode,
            user_pro=str(data["user_pro"]),
            user_con=str(data["user_con"]),
        )
    except (ValueError, KeyError, TypeError) as exc:
        logger.warning("room_metadata_invalid", room=room_name, error=str(exc))
        return _fallback_meta(room_name)
