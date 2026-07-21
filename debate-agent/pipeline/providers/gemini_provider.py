"""Google Gemini provider (default) — free AI Studio tier friendly.

Extraction uses native structured output (JSON mime type + response schema).
Verification uses Google Search grounding; the API cannot combine grounding
with JSON response mode, so the first call prompts for strict JSON in text and
reuses the shared parse -> one re-ask -> PipelineError flow (the re-ask runs
without the tool, so it CAN use native JSON mode). Sources come from
grounding_metadata (Google redirect URIs are expected; titles are shown), with
the model's self-reported sources as a fallback only.
"""

import asyncio
import json
import os
import re

import structlog
from google import genai
from google.genai import errors as genai_errors
from google.genai import types

from pipeline.llm_json import (
    MAX_SOURCES,
    REASK_PROMPT,
    parse_json_object,
    validate_verdict_payload,
)
from pipeline.models import PipelineError, Verdict
from pipeline.providers.base import LLMProvider

logger = structlog.get_logger(__name__)

DEFAULT_EXTRACTION_MODEL = "gemini-2.5-flash-lite"
DEFAULT_VERIFICATION_MODEL = "gemini-2.5-flash"
MAX_CLAIMS = 2
VERIFY_TIMEOUT_SECONDS = 25.0
QUOTA_RETRY_MAX_DELAY_SECONDS = 10.0
QUOTA_BUSY_MESSAGE = "Fact-checker is busy — try again in a minute."

# Patchable in tests so the quota retry doesn't really sleep.
_sleep = asyncio.sleep

EXTRACTION_SYSTEM = """You extract checkable factual claims from live political-debate transcripts.

Given the debate topic and a transcript window from one speaker, return a JSON object
with a "claims" array of strings.

Rules:
- At most 2 claims; return {"claims": []} if nothing is checkable.
- Only objectively checkable factual, statistical, or historical claims.
- Exclude opinions, predictions, value judgments, rhetorical questions, and personal anecdotes.
- Rewrite each claim to be fully self-contained: resolve pronouns and vague references
  ("it", "they", "this law") using the debate topic and surrounding text.
- Prefer the most consequential, most checkable claims in the window."""

VERIFICATION_SYSTEM = """You are a neutral, nonpartisan fact-checker for live political debates.

Verify the single claim you are given. Use Google Search to confirm facts,
statistics, or events; prefer primary and independent sources (government
statistics, peer-reviewed research, official records, established wire
services) over partisan or advocacy sources.

Be liberal with "unverifiable": contested framing, value judgments, predictions,
cherry-picked or ambiguous statistics, and claims with thin or conflicting evidence
should be "unverifiable" rather than forced into true/false.

Verdict meanings:
- "true": the claim is accurate as stated.
- "false": the claim is contradicted by reliable evidence.
- "misleading": contains a kernel of truth but omits context that changes the picture.
- "unverifiable": cannot be responsibly settled (see above).

After your research, output STRICT JSON only — no prose before or after, no code fences:
{"verdict":"true"|"false"|"misleading"|"unverifiable",
 "confidence":"high"|"medium"|"low",
 "summary":"<1-2 sentences, neutral tone>",
 "sources":[{"title":"...","url":"..."}]}

Sources: at most 3, taken only from search results you actually relied on.
Use an empty list when you did not rely on any source (e.g. unverifiable)."""

_EXTRACTION_SCHEMA = types.Schema(
    type=types.Type.OBJECT,
    properties={
        "claims": types.Schema(type=types.Type.ARRAY, items=types.Schema(type=types.Type.STRING))
    },
    required=["claims"],
)

_VERDICT_SCHEMA = types.Schema(
    type=types.Type.OBJECT,
    properties={
        "verdict": types.Schema(
            type=types.Type.STRING, enum=["true", "false", "misleading", "unverifiable"]
        ),
        "confidence": types.Schema(type=types.Type.STRING, enum=["high", "medium", "low"]),
        "summary": types.Schema(type=types.Type.STRING),
        "sources": types.Schema(
            type=types.Type.ARRAY,
            items=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "title": types.Schema(type=types.Type.STRING),
                    "url": types.Schema(type=types.Type.STRING),
                },
            ),
        ),
    },
    required=["verdict", "confidence", "summary"],
)


def _is_quota_error(exc: Exception) -> bool:
    return isinstance(exc, genai_errors.APIError) and (
        getattr(exc, "code", None) == 429 or getattr(exc, "status", None) == "RESOURCE_EXHAUSTED"
    )


def _retry_delay_seconds(exc: Exception) -> "float | None":
    """Pull RetryInfo.retryDelay (e.g. "7s", "3.5s") out of the error payload."""
    details = getattr(exc, "details", None)
    if isinstance(details, dict):
        details = details.get("error", details)
        entries = details.get("details") if isinstance(details, dict) else None
    elif isinstance(details, list):
        entries = details
    else:
        entries = None
    for entry in entries or []:
        if isinstance(entry, dict) and str(entry.get("@type", "")).endswith("RetryInfo"):
            match = re.fullmatch(r"([0-9.]+)s", str(entry.get("retryDelay", "")))
            if match:
                return float(match.group(1))
    return None


def _usage_dict(response) -> dict:
    meta = getattr(response, "usage_metadata", None)
    usage = {
        "input_tokens": getattr(meta, "prompt_token_count", 0) or 0,
        "output_tokens": getattr(meta, "candidates_token_count", 0) or 0,
    }
    thoughts = getattr(meta, "thoughts_token_count", None)
    if thoughts:
        usage["thought_tokens"] = thoughts
    tool_tokens = getattr(meta, "tool_use_prompt_token_count", None)
    if tool_tokens:
        usage["tool_use_prompt_tokens"] = tool_tokens
    return usage


def _add_usage(a: dict, b: dict) -> dict:
    merged = dict(a)
    for key, value in b.items():
        merged[key] = merged.get(key, 0) + value
    return merged


def _grounding_sources(response) -> "list[dict]":
    candidates = getattr(response, "candidates", None) or []
    if not candidates:
        return []
    metadata = getattr(candidates[0], "grounding_metadata", None)
    chunks = (getattr(metadata, "grounding_chunks", None) or []) if metadata else []
    sources = []
    for chunk in chunks:
        web = getattr(chunk, "web", None)
        uri = getattr(web, "uri", None)
        if not uri:
            continue
        sources.append({"title": getattr(web, "title", None) or uri, "url": uri})
        if len(sources) == MAX_SOURCES:
            break
    return sources


class GeminiProvider(LLMProvider):
    name = "gemini"

    def __init__(self, client=None, api_key: "str | None" = None):
        self.extraction_model = os.getenv("GEMINI_EXTRACTION_MODEL", DEFAULT_EXTRACTION_MODEL)
        self.verification_model = os.getenv(
            "GEMINI_VERIFICATION_MODEL", DEFAULT_VERIFICATION_MODEL
        )
        if client is not None:
            self._client = client
        else:
            key = api_key or os.getenv("GEMINI_API_KEY")
            if not key:
                raise RuntimeError(
                    "GEMINI_API_KEY is not set — get a free key at "
                    "https://aistudio.google.com/apikey (or set LLM_PROVIDER=anthropic)"
                )
            self._client = genai.Client(api_key=key)

    # ------------------------------------------------------------- transport

    async def _generate(self, *, model: str, contents, config, purpose: str):
        """generate_content with 404 guarding and free-tier quota handling:
        one short-delay retry when the error names a delay <= 10s."""
        try:
            return await self._client.aio.models.generate_content(
                model=model, contents=contents, config=config
            )
        except genai_errors.APIError as exc:
            if getattr(exc, "code", None) == 404:
                # Never silently substitute a different model.
                logger.error(
                    "gemini_model_not_found",
                    model=model,
                    hint="list models via client.aio.models.list() or see ai.google.dev",
                )
                raise PipelineError("Fact-checking is temporarily misconfigured.") from exc
            if not _is_quota_error(exc):
                raise
            delay = _retry_delay_seconds(exc)
            logger.warning("gemini_quota", model=model, purpose=purpose, retry_delay_s=delay)
            if delay is None or delay > QUOTA_RETRY_MAX_DELAY_SECONDS:
                raise PipelineError(QUOTA_BUSY_MESSAGE) from exc
            await _sleep(delay)
            try:
                return await self._client.aio.models.generate_content(
                    model=model, contents=contents, config=config
                )
            except genai_errors.APIError as exc2:
                if _is_quota_error(exc2):
                    logger.warning(
                        "gemini_quota", model=model, purpose=purpose, retry="exhausted"
                    )
                    raise PipelineError(QUOTA_BUSY_MESSAGE) from exc2
                raise

    # ------------------------------------------------------------ extraction

    async def extract_claims(self, topic: str, window_text: str) -> "tuple[list[str], dict]":
        prompt = f"Debate topic: {topic}\n\nTranscript window (one speaker):\n{window_text}"
        config = types.GenerateContentConfig(
            system_instruction=EXTRACTION_SYSTEM,
            response_mime_type="application/json",
            response_schema=_EXTRACTION_SCHEMA,
        )
        try:
            response = await self._generate(
                model=self.extraction_model, contents=prompt, config=config, purpose="extraction"
            )
        except genai_errors.APIError as exc:
            logger.warning("extraction_api_error", error=str(exc))
            raise PipelineError("The fact-checker is unavailable right now.") from exc
        usage = _usage_dict(response)
        try:
            data = json.loads(response.text or "")
            claims_raw = data.get("claims") if isinstance(data, dict) else None
            if not isinstance(claims_raw, list):
                raise ValueError('"claims" must be a list')
        except (json.JSONDecodeError, ValueError) as exc:
            # Structured output should make this impossible; no re-ask needed.
            logger.error("gemini_extraction_malformed", error=str(exc))
            raise PipelineError(
                "The fact-checker could not read that part of the debate."
            ) from exc
        claims = [c.strip() for c in claims_raw if isinstance(c, str) and c.strip()][:MAX_CLAIMS]
        return claims, usage

    # ---------------------------------------------------------- verification

    async def _verify_once(self, topic: str, claim: str) -> "tuple[Verdict, dict]":
        user_prompt = f"Debate topic: {topic}\n\nClaim to verify: {claim}"
        config = types.GenerateContentConfig(
            system_instruction=VERIFICATION_SYSTEM,
            tools=[types.Tool(google_search=types.GoogleSearch())],
        )
        grounded_response = await self._generate(
            model=self.verification_model,
            contents=user_prompt,
            config=config,
            purpose="verification",
        )
        usage = _usage_dict(grounded_response)
        text = grounded_response.text or ""
        try:
            data = validate_verdict_payload(parse_json_object(text))
        except (json.JSONDecodeError, ValueError) as exc:
            logger.warning(
                "llm_json_malformed", model=self.verification_model, error=str(exc), reasking=True
            )
            # One re-ask demanding valid JSON. No grounding tool this time, so
            # native JSON mode is allowed and enforces the schema.
            reask_contents = [
                types.Content(role="user", parts=[types.Part(text=user_prompt)]),
                types.Content(role="model", parts=[types.Part(text=text or "(empty reply)")]),
                types.Content(role="user", parts=[types.Part(text=REASK_PROMPT)]),
            ]
            reask_config = types.GenerateContentConfig(
                system_instruction=VERIFICATION_SYSTEM,
                response_mime_type="application/json",
                response_schema=_VERDICT_SCHEMA,
            )
            reask_response = await self._generate(
                model=self.verification_model,
                contents=reask_contents,
                config=reask_config,
                purpose="verification_reask",
            )
            usage = _add_usage(usage, _usage_dict(reask_response))
            try:
                data = validate_verdict_payload(parse_json_object(reask_response.text or ""))
            except (json.JSONDecodeError, ValueError) as exc2:
                logger.error(
                    "llm_json_malformed_after_reask",
                    model=self.verification_model,
                    error=str(exc2),
                )
                raise PipelineError("The fact-checker returned an unreadable answer.") from exc2

        # Grounding metadata (from the grounded first call) beats the model's
        # self-reported sources, which are a fallback only.
        grounded_sources = _grounding_sources(grounded_response)
        sources = grounded_sources if grounded_sources else data["sources"]
        verdict = Verdict(
            claim=claim,
            verdict=data["verdict"],
            confidence=data["confidence"],
            summary=data["summary"],
            sources=sources[:MAX_SOURCES],
        )
        return verdict, usage

    async def verify_claim(
        self, topic: str, claim: str, *, timeout: float = VERIFY_TIMEOUT_SECONDS
    ) -> "tuple[Verdict, dict]":
        """Same envelope as the Anthropic path: 25s timeout, one retry, then
        PipelineError (quota errors surface immediately with their own message)."""
        last_error: "Exception | None" = None
        for attempt in (1, 2):
            try:
                return await asyncio.wait_for(self._verify_once(topic, claim), timeout=timeout)
            except PipelineError:
                raise
            except asyncio.TimeoutError as exc:
                logger.warning(
                    "verification_attempt_failed", attempt=attempt, error="timeout"
                )
                last_error = exc
            except genai_errors.APIError as exc:
                logger.warning("verification_attempt_failed", attempt=attempt, error=repr(exc))
                last_error = exc
        raise PipelineError("The fact-check took too long. Try again in a moment.") from last_error

    # -------------------------------------------------------------- generic

    async def complete_json(
        self,
        system: str,
        user: str,
        *,
        image_bytes: "bytes | None" = None,
        image_mime: str = "image/jpeg",
        model: "str | None" = None,
    ) -> "tuple[dict, dict]":
        parts = [types.Part(text=user)]
        if image_bytes is not None:
            parts.insert(0, types.Part.from_bytes(data=image_bytes, mime_type=image_mime))
        config = types.GenerateContentConfig(
            system_instruction=system, response_mime_type="application/json"
        )
        try:
            response = await self._generate(
                model=model or self.extraction_model,
                contents=[types.Content(role="user", parts=parts)],
                config=config,
                purpose="complete_json",
            )
        except genai_errors.APIError as exc:
            logger.warning("complete_json_api_error", error=str(exc))
            raise PipelineError("The model is unavailable right now.") from exc
        usage = _usage_dict(response)
        try:
            return parse_json_object(response.text or ""), usage
        except (json.JSONDecodeError, ValueError) as exc:
            raise PipelineError("The model returned an unreadable answer.") from exc

    async def aclose(self) -> None:
        aio = getattr(self._client, "aio", None)
        close = getattr(aio, "aclose", None)
        if close is None:
            return
        try:
            result = close()
            if asyncio.iscoroutine(result):
                await result
        except Exception:  # cleanup is best-effort
            pass
