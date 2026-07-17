"""Claim extraction: transcript window -> up to 2 self-contained checkable claims."""

import anthropic
import structlog

from pipeline.llm_json import MalformedResponseError, request_strict_json
from pipeline.models import PipelineError

logger = structlog.get_logger(__name__)

EXTRACTION_MODEL = "claude-haiku-4-5-20251001"
MAX_CLAIMS = 2

EXTRACTION_SYSTEM = """You extract checkable factual claims from live political-debate transcripts.

Given the debate topic and a transcript window from one speaker, return ONLY a JSON object:
{"claims": ["<claim 1>", "<claim 2>"]}

Rules:
- At most 2 claims; return {"claims": []} if nothing is checkable.
- Only objectively checkable factual, statistical, or historical claims.
- Exclude opinions, predictions, value judgments, rhetorical questions, and personal anecdotes.
- Rewrite each claim to be fully self-contained: resolve pronouns and vague references
  ("it", "they", "this law") using the debate topic and surrounding text.
- Prefer the most consequential, most checkable claims in the window.
- Output the JSON object only — no prose, no code fences."""


def _validate_extraction(data: dict) -> dict:
    claims = data.get("claims")
    if not isinstance(claims, list):
        raise ValueError('"claims" must be a list')
    cleaned = [c.strip() for c in claims if isinstance(c, str) and c.strip()]
    return {"claims": cleaned[:MAX_CLAIMS]}


async def extract_claims(client, topic: str, window_text: str) -> "tuple[list[str], dict]":
    """Returns (claims, usage). Raises PipelineError on unusable model output."""
    prompt = f"Debate topic: {topic}\n\nTranscript window (one speaker):\n{window_text}"
    try:
        data, usage = await request_strict_json(
            client,
            model=EXTRACTION_MODEL,
            system=EXTRACTION_SYSTEM,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=500,
            validate=_validate_extraction,
        )
    except MalformedResponseError as exc:
        raise PipelineError("The fact-checker could not read that part of the debate.") from exc
    except anthropic.NotFoundError as exc:
        # Do not silently substitute a different model tier.
        logger.error(
            "extraction_model_not_found",
            model=EXTRACTION_MODEL,
            hint="model id 404 — check docs.claude.com for current model ids",
        )
        raise PipelineError("Fact-checking is temporarily misconfigured.") from exc
    except anthropic.APIError as exc:
        logger.warning("extraction_api_error", error=str(exc))
        raise PipelineError("The fact-checker is unavailable right now.") from exc
    return data["claims"], usage
