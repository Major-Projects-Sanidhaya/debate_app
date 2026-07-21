"""Anthropic provider — the original implementation, moved here unchanged.

Claim extraction on claude-haiku-4-5-20251001, verification on
claude-sonnet-4-6 with the web_search server tool, and the shared strict-JSON
parse -> one re-ask -> PipelineError flow from pipeline.llm_json.
"""

import asyncio
import base64

import anthropic
import structlog

from pipeline.llm_json import (
    MalformedResponseError,
    request_strict_json,
    validate_verdict_payload,
)
from pipeline.models import PipelineError, Verdict
from pipeline.providers.base import LLMProvider

logger = structlog.get_logger(__name__)

EXTRACTION_MODEL = "claude-haiku-4-5-20251001"
VERIFICATION_MODEL = "claude-sonnet-4-6"
MAX_CLAIMS = 2
VERIFY_TIMEOUT_SECONDS = 25.0

WEB_SEARCH_TOOL = {"type": "web_search_20250305", "name": "web_search", "max_uses": 3}

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


async def _verify_once(client, topic: str, claim: str) -> "tuple[Verdict, dict]":
    data, usage = await request_strict_json(
        client,
        model=VERIFICATION_MODEL,
        system=VERIFICATION_SYSTEM,
        messages=[{"role": "user", "content": f"Debate topic: {topic}\n\nClaim to verify: {claim}"}],
        max_tokens=1000,
        tools=[WEB_SEARCH_TOOL],
        validate=validate_verdict_payload,
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


class AnthropicProvider(LLMProvider):
    name = "anthropic"
    extraction_model = EXTRACTION_MODEL
    verification_model = VERIFICATION_MODEL

    def __init__(self, client=None):
        self._client = client or anthropic.AsyncAnthropic()

    async def extract_claims(self, topic: str, window_text: str) -> "tuple[list[str], dict]":
        return await extract_claims(self._client, topic, window_text)

    async def verify_claim(self, topic: str, claim: str) -> "tuple[Verdict, dict]":
        return await verify_claim(self._client, topic, claim)

    async def complete_json(
        self,
        system: str,
        user: str,
        *,
        image_bytes: "bytes | None" = None,
        image_mime: str = "image/jpeg",
        model: "str | None" = None,
        max_tokens: int = 1000,
    ) -> "tuple[dict, dict]":
        content: "list | str"
        if image_bytes is not None:
            content = [
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": image_mime,
                        "data": base64.standard_b64encode(image_bytes).decode("ascii"),
                    },
                },
                {"type": "text", "text": user},
            ]
        else:
            content = user
        try:
            return await request_strict_json(
                self._client,
                model=model or EXTRACTION_MODEL,
                system=system,
                messages=[{"role": "user", "content": content}],
                max_tokens=max_tokens,
                validate=lambda d: d,
            )
        except MalformedResponseError as exc:
            raise PipelineError("The model returned an unreadable answer.") from exc
        except anthropic.APIError as exc:
            logger.warning("complete_json_api_error", error=str(exc))
            raise PipelineError("The model is unavailable right now.") from exc

    async def aclose(self) -> None:
        try:
            await self._client.close()
        except Exception:  # cleanup is best-effort
            pass
