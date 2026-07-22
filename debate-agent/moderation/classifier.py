"""Content classification for transcript windows and video frames.

Runs through the active LLM provider's generic `complete_json` helper, so
moderation follows LLM_PROVIDER like the rest of the pipeline. On the
anthropic provider that is claude-haiku-4-5-20251001 (text and vision) —
`complete_json`'s default model — and on the gemini default it is the
provider's configured fast model.

Malformed or off-contract output gets exactly one re-ask demanding valid
JSON; after that the screening is dropped and logged. Nothing here raises.
"""

from dataclasses import dataclass

import structlog

from pipeline.models import PipelineError

logger = structlog.get_logger(__name__)

TEXT_CATEGORIES = (
    "harassment_hate",
    "sexual_content",
    "minor_safety",
    "violence_threat",
    "self_harm",
    "none",
)
VIDEO_CATEGORIES = ("sexual_content", "violence_gore", "none")
SEVERITIES = ("none", "medium", "severe")

# debate-api's contract has no "violence_gore"; frames map onto violence_threat.
VIDEO_CATEGORY_MAP = {"violence_gore": "violence_threat"}

REASK = (
    "Your previous reply was not valid JSON matching the required schema. "
    "Respond again with ONLY the JSON object — no prose, no code fences."
)

TEXT_SYSTEM = """You screen live political-debate transcripts for content-policy violations.

This is a political debate between strangers who disagree. Heated argument, harsh
criticism of ideas or policies, insults aimed at positions or public figures,
sarcasm, mockery, and profanity are NOT violations. Do not flag them.

Flag only:
- harassment_hate: sustained targeted abuse of the other debater, or hatred directed
  at people for who they are (race, religion, ethnicity, national origin, gender,
  sexual orientation, disability).
- sexual_content: sexual propositions or explicit sexual description.
- minor_safety: anything sexual or predatory involving minors, or a speaker who
  appears to be a minor.
- violence_threat: a credible threat of violence against a real person.
- self_harm: statements indicating genuine risk of suicide or self-harm.

Severity:
- "medium": violating, but not immediately dangerous.
- "severe": explicit sexual content, anything involving minors, a credible violent
  threat, active self-harm risk, or relentless targeted abuse.

Respond with ONLY this JSON object and nothing else:
{"category":"harassment_hate"|"sexual_content"|"minor_safety"|"violence_threat"|"self_harm"|"none",
 "severity":"none"|"medium"|"severe"}

When there is no violation, respond {"category":"none","severity":"none"}."""

VIDEO_SYSTEM = """You screen a single still frame from a live video debate for policy violations.

People talking on camera, facial expressions, hand gestures, ordinary clothing, and
everyday rooms or backgrounds are NOT violations. Do not flag them.

Flag only:
- sexual_content: nudity or sexual activity.
- violence_gore: graphic violence, gore, or a weapon brandished at the camera.

Severity:
- "medium": partial or ambiguous (e.g. partial nudity, a weapon merely visible).
- "severe": explicit nudity or sexual activity, or graphic violence or gore.

Respond with ONLY this JSON object and nothing else:
{"category":"sexual_content"|"violence_gore"|"none","severity":"none"|"medium"|"severe"}

When there is no violation, respond {"category":"none","severity":"none"}."""


@dataclass
class Screening:
    category: str
    severity: str

    @property
    def is_violation(self) -> bool:
        return self.category != "none" and self.severity != "none"


def _validate(data: dict, allowed_categories: "tuple[str, ...]") -> Screening:
    category = data.get("category")
    severity = data.get("severity")
    if category not in allowed_categories:
        raise ValueError(f"invalid category: {category!r}")
    if severity not in SEVERITIES:
        raise ValueError(f"invalid severity: {severity!r}")
    # A category without a severity (or vice versa) is incoherent; treat the
    # pair as clean rather than inventing a level.
    if (category == "none") != (severity == "none"):
        raise ValueError(f"incoherent pair: {category!r}/{severity!r}")
    return Screening(category=category, severity=severity)


async def _classify(
    provider,
    *,
    system: str,
    user: str,
    allowed_categories: "tuple[str, ...]",
    image_bytes: "bytes | None" = None,
    kind: str,
) -> "Screening | None":
    """Returns a Screening, or None when the model output is unusable."""
    last_error = ""
    for attempt in (1, 2):
        prompt = user if attempt == 1 else f"{user}\n\n{REASK}"
        try:
            data, _usage = await provider.complete_json(
                system, prompt, image_bytes=image_bytes, image_mime="image/jpeg"
            )
            return _validate(data, allowed_categories)
        except (PipelineError, ValueError) as exc:
            last_error = str(exc)
            logger.warning(
                "moderation_classify_malformed", kind=kind, attempt=attempt, error=last_error
            )
        except Exception as exc:  # provider/transport trouble — never propagate
            last_error = repr(exc)
            logger.warning("moderation_classify_error", kind=kind, attempt=attempt, error=last_error)
    logger.warning("moderation_classify_dropped", kind=kind, error=last_error)
    return None


async def classify_text(provider, window_text: str) -> "Screening | None":
    return await _classify(
        provider,
        system=TEXT_SYSTEM,
        user=f"Transcript window (one speaker):\n{window_text}",
        allowed_categories=TEXT_CATEGORIES,
        kind="transcript",
    )


async def classify_frame(provider, jpeg_bytes: bytes) -> "Screening | None":
    return await _classify(
        provider,
        system=VIDEO_SYSTEM,
        user="Screen this video frame.",
        allowed_categories=VIDEO_CATEGORIES,
        image_bytes=jpeg_bytes,
        kind="video",
    )
