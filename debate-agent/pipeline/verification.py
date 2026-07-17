"""Claim verification: web-search-grounded verdict via claude-sonnet-4-6."""

import asyncio

import anthropic
import structlog

from pipeline.llm_json import MalformedResponseError, request_strict_json
from pipeline.models import CONFIDENCES, VERDICTS, PipelineError, Verdict

logger = structlog.get_logger(__name__)

VERIFICATION_MODEL = "claude-sonnet-4-6"
VERIFY_TIMEOUT_SECONDS = 25.0
MAX_SOURCES = 3

WEB_SEARCH_TOOL = {"type": "web_search_20250305", "name": "web_search", "max_uses": 3}

VERIFICATION_SYSTEM = """You are a neutral, nonpartisan fact-checker for live political debates.

Verify the single claim you are given. Use web search when the claim involves facts,
statistics, or events you should confirm; prefer primary and independent sources
(government statistics, peer-reviewed research, official records, established wire
services) over partisan or advocacy sources.

Be liberal with "unverifiable": contested framing, value judgments, predictions,
cherry-picked or ambiguous statistics, and claims with thin or conflicting evidence
should be "unverifiable" rather than forced into true/false.

Verdict meanings:
- "true": the claim is accurate as stated.
- "false": the claim is contradicted by reliable evidence.
- "misleading": contains a kernel of truth but omits context that changes the picture.
- "unverifiable": cannot be responsibly settled (see above).

After your research, output STRICT JSON only — no prose before or after:
{"verdict":"true"|"false"|"misleading"|"unverifiable",
 "confidence":"high"|"medium"|"low",
 "summary":"<1-2 sentences, neutral tone>",
 "sources":[{"title":"...","url":"..."}]}

Sources: at most 3, taken only from search results you actually relied on.
Use an empty list when you did not rely on any source (e.g. unverifiable)."""


def _validate_verdict(data: dict) -> dict:
    if data.get("verdict") not in VERDICTS:
        raise ValueError(f"invalid verdict: {data.get('verdict')!r}")
    if data.get("confidence") not in CONFIDENCES:
        raise ValueError(f"invalid confidence: {data.get('confidence')!r}")
    summary = data.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        raise ValueError("missing summary")
    raw_sources = data.get("sources", [])
    if not isinstance(raw_sources, list):
        raise ValueError('"sources" must be a list')
    sources = []
    for entry in raw_sources[:MAX_SOURCES]:
        if (
            isinstance(entry, dict)
            and isinstance(entry.get("title"), str)
            and isinstance(entry.get("url"), str)
        ):
            sources.append({"title": entry["title"], "url": entry["url"]})
    return {
        "verdict": data["verdict"],
        "confidence": data["confidence"],
        "summary": summary.strip(),
        "sources": sources,
    }


async def _verify_once(client, topic: str, claim: str) -> "tuple[Verdict, dict]":
    data, usage = await request_strict_json(
        client,
        model=VERIFICATION_MODEL,
        system=VERIFICATION_SYSTEM,
        messages=[{"role": "user", "content": f"Debate topic: {topic}\n\nClaim to verify: {claim}"}],
        max_tokens=1000,
        tools=[WEB_SEARCH_TOOL],
        validate=_validate_verdict,
    )
    return (
        Verdict(
            claim=claim,
            verdict=data["verdict"],
            confidence=data["confidence"],
            summary=data["summary"],
            sources=data["sources"],
        ),
        usage,
    )


async def verify_claim(
    client, topic: str, claim: str, *, timeout: float = VERIFY_TIMEOUT_SECONDS
) -> "tuple[Verdict, dict]":
    """25s timeout, one retry, then PipelineError (surfaces as fact_check_error)."""
    last_error: "Exception | None" = None
    for attempt in (1, 2):
        try:
            return await asyncio.wait_for(_verify_once(client, topic, claim), timeout=timeout)
        except anthropic.NotFoundError as exc:
            # Do not silently substitute a different model tier.
            logger.error(
                "verification_model_not_found",
                model=VERIFICATION_MODEL,
                hint="model id 404 — check docs.claude.com for current model ids",
            )
            raise PipelineError("Fact-checking is temporarily misconfigured.") from exc
        except MalformedResponseError as exc:
            raise PipelineError("The fact-checker returned an unreadable answer.") from exc
        except (asyncio.TimeoutError, anthropic.APIError) as exc:
            logger.warning("verification_attempt_failed", attempt=attempt, error=repr(exc))
            last_error = exc
    raise PipelineError("The fact-check took too long. Try again in a moment.") from last_error
