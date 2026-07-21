"""GeminiProvider behavior against scripted fakes, plus provider selection."""

import pytest
from google.genai import errors as genai_errors

import pipeline.providers.gemini_provider as gemini_module
from pipeline.models import PipelineError
from pipeline.providers import get_provider
from pipeline.providers.anthropic_provider import AnthropicProvider
from pipeline.providers.gemini_provider import GeminiProvider
from tests.conftest import FakeGeminiClient, gemini_chunk, gemini_response

GOOD_VERDICT_JSON = (
    '{"verdict":"false","confidence":"high",'
    '"summary":"FBI data shows violent crime fell since 1991.",'
    '"sources":[{"title":"Model Source","url":"https://model.example"}]}'
)


def make_provider(script):
    client = FakeGeminiClient(script)
    return GeminiProvider(client=client), client.aio.models


def quota_error(delay=None):
    details = (
        [{"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": f"{delay}s"}]
        if delay is not None
        else []
    )
    return genai_errors.APIError(
        429, {"error": {"message": "quota", "status": "RESOURCE_EXHAUSTED", "details": details}}
    )


def not_found_error():
    return genai_errors.APIError(
        404, {"error": {"message": "model not found", "status": "NOT_FOUND"}}
    )


# ------------------------------------------------------------------ extraction


async def test_extraction_uses_structured_output_and_caps_claims():
    provider, models = make_provider([gemini_response('{"claims": ["A is 1", "B is 2", "C"]}')])
    claims, usage = await provider.extract_claims("Topic", "window")
    assert claims == ["A is 1", "B is 2"]
    assert usage["input_tokens"] == 100 and usage["output_tokens"] == 50
    call = models.calls[0]
    assert call["model"] == "gemini-2.5-flash-lite"
    assert call["config"].response_mime_type == "application/json"
    assert call["config"].response_schema is not None
    assert call["config"].tools is None  # extraction never gets tools


async def test_extraction_malformed_raises_pipeline_error():
    provider, _ = make_provider([gemini_response("not json at all")])
    with pytest.raises(PipelineError):
        await provider.extract_claims("Topic", "window")


async def test_extraction_model_env_override(monkeypatch):
    monkeypatch.setenv("GEMINI_EXTRACTION_MODEL", "gemini-custom")
    provider, models = make_provider([gemini_response('{"claims": []}')])
    assert provider.extraction_model == "gemini-custom"
    await provider.extract_claims("T", "w")
    assert models.calls[0]["model"] == "gemini-custom"


# ---------------------------------------------------------------- verification


async def test_verify_grounded_sources_override_model_sources():
    provider, models = make_provider(
        [
            gemini_response(
                GOOD_VERDICT_JSON,
                grounding=[
                    gemini_chunk("Grounded A", "https://redirect.google/a"),
                    gemini_chunk("Grounded B", "https://redirect.google/b"),
                ],
                thoughts=7,
            )
        ]
    )
    verdict, usage = await provider.verify_claim("Gun control", "Crime rose since 1991")
    assert verdict.verdict == "false"
    assert verdict.sources == [
        {"title": "Grounded A", "url": "https://redirect.google/a"},
        {"title": "Grounded B", "url": "https://redirect.google/b"},
    ]
    assert usage["thought_tokens"] == 7
    call = models.calls[0]
    assert call["model"] == "gemini-2.5-flash"
    assert call["config"].tools is not None  # google_search grounding enabled
    assert call["config"].response_mime_type is None  # JSON mode can't join grounding


async def test_verify_falls_back_to_model_sources_without_grounding():
    provider, _ = make_provider([gemini_response(GOOD_VERDICT_JSON, grounding=None)])
    verdict, _ = await provider.verify_claim("T", "C")
    assert verdict.sources == [{"title": "Model Source", "url": "https://model.example"}]


async def test_verify_grounded_sources_capped_at_three():
    chunks = [gemini_chunk(f"S{i}", f"https://g/{i}") for i in range(5)]
    provider, _ = make_provider([gemini_response(GOOD_VERDICT_JSON, grounding=chunks)])
    verdict, _ = await provider.verify_claim("T", "C")
    assert len(verdict.sources) == 3


async def test_verify_reask_keeps_grounding_from_first_response():
    provider, models = make_provider(
        [
            gemini_response(
                "Well, based on my research it seems false.",
                grounding=[gemini_chunk("Grounded", "https://g")],
            ),
            gemini_response(GOOD_VERDICT_JSON),
        ]
    )
    verdict, _ = await provider.verify_claim("T", "C")
    assert verdict.verdict == "false"
    assert verdict.sources == [{"title": "Grounded", "url": "https://g"}]
    assert len(models.calls) == 2
    reask = models.calls[1]
    # The re-ask drops the grounding tool, so native JSON mode is back on.
    assert reask["config"].tools is None
    assert reask["config"].response_mime_type == "application/json"
    assert reask["config"].response_schema is not None


async def test_verify_reask_failure_raises_unreadable():
    provider, models = make_provider(
        [gemini_response("prose"), gemini_response("still prose")]
    )
    with pytest.raises(PipelineError) as excinfo:
        await provider.verify_claim("T", "C")
    assert "unreadable" in excinfo.value.user_message
    assert len(models.calls) == 2  # exactly one re-ask


# ---------------------------------------------------------------- quota / 404


async def test_quota_with_short_delay_retries_once(monkeypatch):
    sleeps = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)

    monkeypatch.setattr(gemini_module, "_sleep", fake_sleep)
    provider, models = make_provider([quota_error(3), gemini_response('{"claims": []}')])
    claims, _ = await provider.extract_claims("T", "w")
    assert claims == []
    assert sleeps == [3.0]
    assert len(models.calls) == 2


async def test_quota_with_long_delay_fails_fast(monkeypatch):
    monkeypatch.setattr(gemini_module, "_sleep", None)  # would explode if awaited
    provider, models = make_provider([quota_error(30)])
    with pytest.raises(PipelineError) as excinfo:
        await provider.extract_claims("T", "w")
    assert "busy" in excinfo.value.user_message
    assert len(models.calls) == 1  # no retry


async def test_quota_without_delay_fails_fast():
    provider, models = make_provider([quota_error(None)])
    with pytest.raises(PipelineError) as excinfo:
        await provider.verify_claim("T", "C")
    assert "busy" in excinfo.value.user_message
    assert len(models.calls) == 1


async def test_quota_twice_fails_after_single_retry(monkeypatch):
    async def fake_sleep(seconds):
        pass

    monkeypatch.setattr(gemini_module, "_sleep", fake_sleep)
    provider, models = make_provider([quota_error(2), quota_error(2)])
    with pytest.raises(PipelineError) as excinfo:
        await provider.extract_claims("T", "w")
    assert "busy" in excinfo.value.user_message
    assert len(models.calls) == 2


async def test_model_404_never_substitutes():
    provider, models = make_provider([not_found_error()])
    with pytest.raises(PipelineError) as excinfo:
        await provider.verify_claim("T", "C")
    assert "misconfigured" in excinfo.value.user_message
    assert len(models.calls) == 1
    assert models.calls[0]["model"] == "gemini-2.5-flash"  # unchanged


# -------------------------------------------------------------- complete_json


async def test_complete_json_with_image_parts():
    provider, models = make_provider([gemini_response('{"ok": true}')])
    data, usage = await provider.complete_json(
        "system prompt", "describe this", image_bytes=b"\x89PNG fake", image_mime="image/png"
    )
    assert data == {"ok": True}
    content = models.calls[0]["contents"][0]
    assert len(content.parts) == 2  # image part + text part
    assert models.calls[0]["config"].response_mime_type == "application/json"


# ---------------------------------------------------------- provider selection


def test_get_provider_defaults_to_gemini(monkeypatch):
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "dummy")
    assert isinstance(get_provider(), GeminiProvider)


def test_get_provider_selects_anthropic(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-dummy")
    assert isinstance(get_provider(), AnthropicProvider)


def test_get_provider_rejects_unknown(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "chatgpt")
    with pytest.raises(ValueError):
        get_provider()


def test_gemini_requires_key(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(RuntimeError) as excinfo:
        GeminiProvider()
    assert "aistudio.google.com" in str(excinfo.value)
