from pipeline.cache import CACHE_TTL_SECONDS, ClaimCache
from pipeline.models import Verdict


def make_verdict(verdict="false"):
    return Verdict(
        claim="Crime rose 500% since 1991",
        verdict=verdict,
        confidence="high",
        summary="FBI data shows violent crime fell substantially since 1991.",
        sources=[{"title": "FBI UCR", "url": "https://ucr.fbi.gov"}],
    )


async def test_put_then_get_roundtrip(fake_redis):
    cache = ClaimCache(fake_redis)
    assert await cache.put("Crime rose 500% since 1991", make_verdict()) is True
    hit = await cache.get("crime rose 500% since 1991!!")  # normalization-equivalent
    assert hit is not None
    assert hit["verdict"] == "false"
    assert hit["sources"][0]["url"] == "https://ucr.fbi.gov"


async def test_ttl_is_72_hours(fake_redis):
    cache = ClaimCache(fake_redis)
    await cache.put("some claim", make_verdict())
    (ttl,) = fake_redis.ttls.values()
    assert ttl == CACHE_TTL_SECONDS == 72 * 3600


async def test_unverifiable_is_never_cached(fake_redis):
    cache = ClaimCache(fake_redis)
    assert await cache.put("the sky is angry", make_verdict("unverifiable")) is False
    assert await cache.get("the sky is angry") is None
    assert fake_redis.store == {}


async def test_miss_returns_none(fake_redis):
    cache = ClaimCache(fake_redis)
    assert await cache.get("never seen") is None
