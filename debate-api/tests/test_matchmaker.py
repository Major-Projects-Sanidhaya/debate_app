"""Unit tests for the atomic pair-or-enqueue Lua logic, against real Redis."""

import uuid

import pytest
import redis.asyncio as aioredis

from app.matchmaking import Matchmaker, resolve_fact_check_mode


@pytest.fixture
async def redis_client(redis_url, clean):
    r = aioredis.from_url(redis_url, decode_responses=True)
    yield r
    await r.aclose()


@pytest.fixture
def mm(redis_client) -> Matchmaker:
    return Matchmaker(redis_client)


def uid() -> str:
    return str(uuid.uuid4())


async def test_pro_and_con_match(mm):
    a, b = uid(), uid()
    assert (await mm.join(a, 1, "pro", "on_demand")).status == "queued"
    outcome = await mm.join(b, 1, "con", "on_demand")
    assert outcome.status == "matched"
    assert outcome.peer_id == a
    assert outcome.peer_mode == "on_demand"


async def test_match_clears_queue_state(mm, redis_client):
    a, b = uid(), uid()
    await mm.join(a, 1, "pro", "auto")
    await mm.join(b, 1, "con", "auto")
    assert await redis_client.llen("q:1:pro") == 0
    assert await redis_client.llen("q:1:con") == 0
    assert not await redis_client.exists(f"inq:{a}")
    assert not await redis_client.exists(f"inq:{b}")


async def test_same_stance_does_not_match(mm):
    assert (await mm.join(uid(), 1, "pro", "on_demand")).status == "queued"
    assert (await mm.join(uid(), 1, "pro", "on_demand")).status == "queued"


async def test_different_topics_do_not_match(mm):
    assert (await mm.join(uid(), 1, "pro", "on_demand")).status == "queued"
    assert (await mm.join(uid(), 2, "con", "on_demand")).status == "queued"


async def test_double_join_rejected(mm):
    a = uid()
    assert (await mm.join(a, 1, "pro", "on_demand")).status == "queued"
    assert (await mm.join(a, 1, "pro", "on_demand")).status == "already_queued"
    # Also rejected across topics/stances: one queue per user.
    assert (await mm.join(a, 2, "con", "auto")).status == "already_queued"


async def test_leave_removes_from_queue(mm, redis_client):
    a = uid()
    await mm.join(a, 1, "pro", "on_demand")
    assert await mm.leave(a) is True
    assert await redis_client.llen("q:1:pro") == 0
    assert not await redis_client.exists(f"inq:{a}")
    # And the user can join again afterwards.
    assert (await mm.join(a, 1, "pro", "on_demand")).status == "queued"


async def test_leave_when_not_queued_is_noop(mm):
    assert await mm.leave(uid()) is False


async def test_stale_entry_skipped_and_cleaned(mm, redis_client):
    ghost = uid()
    await mm.join(ghost, 1, "pro", "on_demand")
    await redis_client.delete(f"inq:{ghost}")  # simulate TTL expiry / crashed replica

    b = uid()
    outcome = await mm.join(b, 1, "con", "on_demand")
    assert outcome.status == "queued"  # ghost must not match
    assert await redis_client.llen("q:1:pro") == 0  # ghost entry discarded


async def test_self_match_guarded(mm, redis_client):
    a = uid()
    # Forge a stale self entry in the opposite-stance queue.
    await redis_client.rpush("q:1:con", a)
    outcome = await mm.join(a, 1, "pro", "on_demand")
    assert outcome.status == "queued"
    assert await redis_client.llen("q:1:con") == 0


def test_fact_check_mode_resolution():
    assert resolve_fact_check_mode("auto", "auto") == "auto"
    assert resolve_fact_check_mode("auto", "on_demand") == "on_demand"
    assert resolve_fact_check_mode("on_demand", "auto") == "on_demand"
    assert resolve_fact_check_mode("on_demand", "on_demand") == "on_demand"


# --- block exclusion (moderation) --------------------------------------------


async def test_blocked_pair_never_matches_forward(mm, redis_client):
    a, b = uid(), uid()
    await redis_client.sadd(f"blocks:{a}", b)  # a blocked b (one-directional set)
    assert (await mm.join(a, 1, "pro", "on_demand")).status == "queued"
    # b is the only con-seeker; a is the only pro candidate but block-excluded.
    assert (await mm.join(b, 1, "con", "on_demand")).status == "queued"
    assert await redis_client.lrange("q:1:pro", 0, -1) == [a]  # a stays put
    assert await redis_client.exists(f"inq:{a}")


async def test_blocked_pair_never_matches_reverse(mm, redis_client):
    # Exclusion must fire regardless of which side recorded the block.
    a, b = uid(), uid()
    await redis_client.sadd(f"blocks:{b}", a)  # b blocked a; a joins/queues first
    await mm.join(a, 1, "pro", "on_demand")
    assert (await mm.join(b, 1, "con", "on_demand")).status == "queued"
    assert await redis_client.lrange("q:1:pro", 0, -1) == [a]


async def test_skipped_candidates_stay_queued_in_order_and_next_eligible_matches(
    mm, redis_client
):
    x, a, b, c = uid(), uid(), uid(), uid()
    # pro queue fills in order: x, a (both blocked by c), then b (eligible).
    await mm.join(x, 1, "pro", "on_demand")
    await mm.join(a, 1, "pro", "on_demand")
    await mm.join(b, 1, "pro", "on_demand")
    await redis_client.sadd(f"blocks:{c}", x)
    await redis_client.sadd(f"blocks:{c}", a)

    outcome = await mm.join(c, 1, "con", "on_demand")
    assert outcome.status == "matched"
    assert outcome.peer_id == b  # first eligible after skipping x and a

    # x and a re-inserted at the head in their original order; b consumed.
    assert await redis_client.lrange("q:1:pro", 0, -1) == [x, a]
    assert await redis_client.exists(f"inq:{x}")
    assert await redis_client.exists(f"inq:{a}")
    assert not await redis_client.exists(f"inq:{b}")
    assert not await redis_client.exists(f"inq:{c}")  # matched, not enqueued


async def test_block_does_not_affect_unrelated_users(mm, redis_client):
    a, b, c = uid(), uid(), uid()
    await redis_client.sadd(f"blocks:{a}", b)  # a<->b blocked, c is unrelated
    await mm.join(a, 1, "pro", "on_demand")
    outcome = await mm.join(c, 1, "con", "on_demand")
    assert outcome.status == "matched" and outcome.peer_id == a
