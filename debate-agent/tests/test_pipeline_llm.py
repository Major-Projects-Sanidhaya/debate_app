"""Extraction/verification behavior against a scripted fake Anthropic client."""

import asyncio

import anthropic
import httpx
import pytest

from pipeline.llm_json import REASK_PROMPT
from pipeline.models import PipelineError
from pipeline.providers.anthropic_provider import extract_claims, verify_claim
from tests.conftest import FakeAnthropic, text_response

GOOD_VERDICT = (
    '{"verdict":"false","confidence":"high",'
    '"summary":"FBI data shows violent crime fell since 1991.",'
    '"sources":[{"title":"FBI UCR","url":"https://ucr.fbi.gov"}]}'
)


def not_found_error():
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    return anthropic.NotFoundError(
        "model not found",
        response=httpx.Response(404, request=request),
        body={"error": {"type": "not_found_error"}},
    )


# ------------------------------------------------------------------ extraction


async def test_extract_parses_and_caps_at_two():
    client = FakeAnthropic([text_response('{"claims": ["A is 1", "B is 2", "C is 3"]}')])
    claims, usage = await extract_claims(client, "Topic", "window")
    assert claims == ["A is 1", "B is 2"]
    assert usage["input_tokens"] == 100
    assert client.messages.calls[0]["model"] == "claude-haiku-4-5-20251001"


async def test_extract_empty_claims_ok():
    client = FakeAnthropic([text_response('{"claims": []}')])
    claims, _ = await extract_claims(client, "Topic", "pure opinion text")
    assert claims == []


async def test_malformed_json_reasks_once_then_succeeds():
    client = FakeAnthropic(
        [text_response("Sure! Here are the claims: A and B"), text_response('{"claims": ["A"]}')]
    )
    claims, _ = await extract_claims(client, "Topic", "window")
    assert claims == ["A"]
    assert len(client.messages.calls) == 2
    # The re-ask carries the bad reply and the corrective instruction.
    reask_messages = client.messages.calls[1]["messages"]
    assert reask_messages[-1]["content"] == REASK_PROMPT
    assert reask_messages[-2]["role"] == "assistant"


async def test_malformed_json_twice_raises_pipeline_error():
    client = FakeAnthropic([text_response("nope"), text_response("still nope")])
    with pytest.raises(PipelineError):
        await extract_claims(client, "Topic", "window")
    assert len(client.messages.calls) == 2  # exactly one re-ask, no more


async def test_extraction_model_404_is_not_silently_substituted():
    client = FakeAnthropic([not_found_error()])
    with pytest.raises(PipelineError):
        await extract_claims(client, "Topic", "window")
    assert len(client.messages.calls) == 1  # no retry with a different model
    assert client.messages.calls[0]["model"] == "claude-haiku-4-5-20251001"


# ---------------------------------------------------------------- verification


async def test_verify_happy_path_uses_web_search_tool():
    client = FakeAnthropic([text_response(GOOD_VERDICT)])
    verdict, usage = await verify_claim(client, "Gun control", "Crime rose since 1991")
    assert verdict.verdict == "false"
    assert verdict.confidence == "high"
    assert verdict.sources == [{"title": "FBI UCR", "url": "https://ucr.fbi.gov"}]
    call = client.messages.calls[0]
    assert call["model"] == "claude-sonnet-4-6"
    assert call["tools"] == [
        {"type": "web_search_20250305", "name": "web_search", "max_uses": 3}
    ]


async def test_verify_clamps_sources_to_three():
    many = (
        '{"verdict":"true","confidence":"medium","summary":"ok.","sources":['
        + ",".join(f'{{"title":"s{i}","url":"https://s{i}"}}' for i in range(5))
        + "]}"
    )
    client = FakeAnthropic([text_response(many)])
    verdict, _ = await verify_claim(client, "T", "C")
    assert len(verdict.sources) == 3


async def test_verify_invalid_enum_triggers_reask():
    bad = '{"verdict":"probably","confidence":"high","summary":"x","sources":[]}'
    client = FakeAnthropic([text_response(bad), text_response(GOOD_VERDICT)])
    verdict, _ = await verify_claim(client, "T", "C")
    assert verdict.verdict == "false"
    assert len(client.messages.calls) == 2


async def test_verify_timeout_retries_once_then_errors():
    async def hang(**kwargs):
        await asyncio.sleep(5)

    client = FakeAnthropic([hang, hang])
    with pytest.raises(PipelineError) as excinfo:
        await verify_claim(client, "T", "C", timeout=0.05)
    assert len(client.messages.calls) == 2  # original + exactly one retry
    assert "too long" in excinfo.value.user_message


async def test_verify_timeout_then_success_on_retry():
    async def hang(**kwargs):
        await asyncio.sleep(5)

    client = FakeAnthropic([hang, text_response(GOOD_VERDICT)])
    verdict, _ = await verify_claim(client, "T", "C", timeout=0.05)
    assert verdict.verdict == "false"


async def test_verification_model_404_is_not_silently_substituted():
    client = FakeAnthropic([not_found_error()])
    with pytest.raises(PipelineError):
        await verify_claim(client, "T", "C")
    assert len(client.messages.calls) == 1
    assert client.messages.calls[0]["model"] == "claude-sonnet-4-6"
