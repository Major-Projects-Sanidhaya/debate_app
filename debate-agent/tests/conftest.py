import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline.models import RoomMeta  # noqa: E402


class FakeRedis:
    """Just enough async redis for ClaimCache and TranscriptMirror."""

    def __init__(self):
        self.store: dict = {}
        self.lists: dict = {}
        self.ttls: dict = {}

    async def get(self, key):
        return self.store.get(key)

    async def set(self, key, value, ex=None):
        self.store[key] = value
        if ex is not None:
            self.ttls[key] = ex

    async def rpush(self, key, value):
        self.lists.setdefault(key, []).append(value)

    async def expire(self, key, ttl):
        self.ttls[key] = ttl


class FakeMessages:
    """Scripted responses: response objects, exceptions, or async callables."""

    def __init__(self, script):
        self.script = list(script)
        self.calls: list = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        if not self.script:
            raise AssertionError("FakeAnthropic ran out of scripted responses")
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        if callable(item):
            return await item(**kwargs)
        return item


class FakeAnthropic:
    def __init__(self, script):
        self.messages = FakeMessages(script)


def text_response(text, input_tokens=100, output_tokens=50):
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text=text)],
        usage=SimpleNamespace(
            input_tokens=input_tokens, output_tokens=output_tokens, server_tool_use=None
        ),
    )


class FakeGeminiModels:
    """Scripted google-genai aio.models: responses, exceptions, or async callables."""

    def __init__(self, script):
        self.script = list(script)
        self.calls: list = []

    async def generate_content(self, *, model, contents, config):
        self.calls.append({"model": model, "contents": contents, "config": config})
        if not self.script:
            raise AssertionError("FakeGemini ran out of scripted responses")
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        if callable(item):
            return await item(model=model, contents=contents, config=config)
        return item


class FakeGeminiClient:
    def __init__(self, script):
        self.aio = SimpleNamespace(models=FakeGeminiModels(script))


def gemini_chunk(title, uri):
    return SimpleNamespace(web=SimpleNamespace(title=title, uri=uri))


def gemini_response(text, grounding=None, prompt_tokens=100, output_tokens=50, thoughts=None):
    metadata = SimpleNamespace(grounding_chunks=grounding) if grounding is not None else None
    return SimpleNamespace(
        text=text,
        candidates=[SimpleNamespace(grounding_metadata=metadata)],
        usage_metadata=SimpleNamespace(
            prompt_token_count=prompt_tokens,
            candidates_token_count=output_tokens,
            thoughts_token_count=thoughts,
            tool_use_prompt_token_count=None,
        ),
    )


class FakeClock:
    def __init__(self, now=1000.0):
        self.now = now

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


@pytest.fixture
def fake_redis():
    return FakeRedis()


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def meta():
    return RoomMeta(
        match_id="match-1",
        topic_id=1,
        topic="Gun control",
        fact_check_mode="on_demand",
        user_pro="uid-pro",
        user_con="uid-con",
    )
