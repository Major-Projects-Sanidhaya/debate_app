"""Strict-JSON calling convention shared by extraction and verification.

Both pipeline stages demand a raw JSON object from the model. If the reply
doesn't parse (or fails the caller's validation), we re-ask exactly once —
appending the bad reply and a corrective user turn — then give up so the
caller can emit fact_check_error.
"""

import json

import structlog

logger = structlog.get_logger(__name__)

REASK_PROMPT = (
    "Your previous reply was not valid JSON matching the required schema. "
    "Respond again with ONLY the JSON object — no prose, no code fences."
)


class MalformedResponseError(Exception):
    pass


def extract_text(response) -> str:
    """Concatenate text blocks; server tool blocks (web search) are skipped."""
    parts = []
    for block in response.content:
        if getattr(block, "type", None) == "text":
            parts.append(block.text)
    return "".join(parts).strip()


def usage_dict(response) -> dict:
    usage = getattr(response, "usage", None)
    out = {
        "input_tokens": getattr(usage, "input_tokens", 0) or 0,
        "output_tokens": getattr(usage, "output_tokens", 0) or 0,
    }
    server = getattr(usage, "server_tool_use", None)
    searches = getattr(server, "web_search_requests", None)
    if searches:
        out["web_search_requests"] = searches
    return out


def add_usage(a: dict, b: dict) -> dict:
    merged = dict(a)
    for key, value in b.items():
        merged[key] = merged.get(key, 0) + value
    return merged


MAX_SOURCES = 3


def validate_verdict_payload(data: dict) -> dict:
    """Contract validation for the verification JSON, shared by all providers.
    (Moved unchanged from the original Anthropic verification module.)"""
    from pipeline.models import CONFIDENCES, VERDICTS

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


def parse_json_object(text: str) -> dict:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        # tolerate fenced output rather than burning a re-ask on it
        cleaned = cleaned.strip("`")
        if cleaned.startswith("json"):
            cleaned = cleaned[4:]
        cleaned = cleaned.strip()
    data = json.loads(cleaned)
    if not isinstance(data, dict):
        raise ValueError("top-level JSON value is not an object")
    return data


async def request_strict_json(
    client,
    *,
    model: str,
    system: str,
    messages: list,
    max_tokens: int,
    validate,
    tools: "list | None" = None,
) -> "tuple[dict, dict]":
    """Call the model, parse+validate strict JSON, re-ask once on failure.

    `validate(data) -> dict` should raise ValueError on contract violations.
    Returns (validated_data, usage_totals).
    """
    kwargs = {"model": model, "max_tokens": max_tokens, "system": system, "messages": messages}
    if tools is not None:
        kwargs["tools"] = tools

    response = await client.messages.create(**kwargs)
    usage = usage_dict(response)
    text = extract_text(response)
    try:
        return validate(parse_json_object(text)), usage
    except (json.JSONDecodeError, ValueError) as exc:
        logger.warning("llm_json_malformed", model=model, error=str(exc), reasking=True)

    # One re-ask demanding valid JSON. The assistant turn sits mid-conversation
    # (not a trailing prefill), which every current model accepts. No tools on
    # the re-ask: it only reformats the answer it already produced.
    reask_messages = messages + [
        {"role": "assistant", "content": text or "(empty reply)"},
        {"role": "user", "content": REASK_PROMPT},
    ]
    response = await client.messages.create(
        model=model, max_tokens=max_tokens, system=system, messages=reask_messages
    )
    usage = add_usage(usage, usage_dict(response))
    text = extract_text(response)
    try:
        return validate(parse_json_object(text)), usage
    except (json.JSONDecodeError, ValueError) as exc:
        logger.error("llm_json_malformed_after_reask", model=model, error=str(exc))
        raise MalformedResponseError(str(exc)) from exc
