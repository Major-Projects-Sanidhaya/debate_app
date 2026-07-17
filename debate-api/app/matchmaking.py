"""Atomic pair-or-enqueue matchmaking on Redis.

State model:
- q:{topic_id}:{stance} — LIST of user_ids waiting on that topic+stance.
- inq:{user_id}         — STRING "<queue_key>|<fact_check_mode>", marks queue
                          membership (at most one queue per user) and carries
                          the user's requested mode for match-time resolution.

Both scripts run atomically in Redis, so pair-or-enqueue stays correct with
multiple API replicas. inq keys carry a TTL and are refreshed while the
owning websocket is alive, so state leaked by a crashed replica self-heals.

Note: queue keys are computed inside the scripts from inq values, which is
fine on a single Redis node but not Redis Cluster compatible.
"""

from dataclasses import dataclass
from typing import Literal

import redis.asyncio as aioredis

INQ_TTL_SECONDS = 300

Stance = Literal["pro", "con"]
Mode = Literal["on_demand", "auto"]

# KEYS[1]=opposite queue, KEYS[2]=own queue, KEYS[3]=inq:{self}
# ARGV[1]=user_id, ARGV[2]=fact_check_mode, ARGV[3]=inq ttl seconds
# Pops the opposite queue until a live peer is found (discarding stale
# entries whose inq is gone or points elsewhere, and self-matches);
# otherwise enqueues self.
_JOIN_LUA = """
if redis.call('EXISTS', KEYS[3]) == 1 then
  return {'already_queued'}
end
while true do
  local peer_id = redis.call('LPOP', KEYS[1])
  if not peer_id then break end
  if peer_id ~= ARGV[1] then
    local peer_inq = 'inq:' .. peer_id
    local v = redis.call('GET', peer_inq)
    if v then
      local sep = string.find(v, '|', 1, true)
      local qkey = string.sub(v, 1, sep - 1)
      local pmode = string.sub(v, sep + 1)
      if qkey == KEYS[1] then
        redis.call('DEL', peer_inq)
        return {'matched', peer_id, pmode}
      end
    end
  end
end
redis.call('RPUSH', KEYS[2], ARGV[1])
redis.call('SET', KEYS[3], KEYS[2] .. '|' .. ARGV[2], 'EX', tonumber(ARGV[3]))
return {'queued'}
"""

# KEYS[1]=inq:{user_id}, ARGV[1]=user_id
_LEAVE_LUA = """
local v = redis.call('GET', KEYS[1])
if not v then return 0 end
local sep = string.find(v, '|', 1, true)
local qkey = string.sub(v, 1, sep - 1)
redis.call('LREM', qkey, 0, ARGV[1])
redis.call('DEL', KEYS[1])
return 1
"""


@dataclass
class JoinOutcome:
    status: Literal["matched", "queued", "already_queued"]
    peer_id: str | None = None
    peer_mode: str | None = None


def resolve_fact_check_mode(mode_a: str, mode_b: str) -> str:
    return "auto" if mode_a == "auto" and mode_b == "auto" else "on_demand"


def opposite(stance: Stance) -> Stance:
    return "con" if stance == "pro" else "pro"


class Matchmaker:
    def __init__(self, redis: aioredis.Redis):
        self._redis = redis
        self._join = redis.register_script(_JOIN_LUA)
        self._leave = redis.register_script(_LEAVE_LUA)

    @staticmethod
    def queue_key(topic_id: int, stance: str) -> str:
        return f"q:{topic_id}:{stance}"

    @staticmethod
    def inq_key(user_id: str) -> str:
        return f"inq:{user_id}"

    async def is_queued(self, user_id: str) -> bool:
        return bool(await self._redis.exists(self.inq_key(user_id)))

    async def join(self, user_id: str, topic_id: int, stance: Stance, mode: Mode) -> JoinOutcome:
        res = await self._join(
            keys=[
                self.queue_key(topic_id, opposite(stance)),
                self.queue_key(topic_id, stance),
                self.inq_key(user_id),
            ],
            args=[user_id, mode, INQ_TTL_SECONDS],
        )
        status = res[0]
        if status == "matched":
            return JoinOutcome(status="matched", peer_id=res[1], peer_mode=res[2])
        return JoinOutcome(status=status)

    async def leave(self, user_id: str) -> bool:
        """Remove the user from whatever queue they're in. Safe to call when not queued."""
        return bool(await self._leave(keys=[self.inq_key(user_id)], args=[user_id]))

    async def refresh(self, user_id: str) -> bool:
        """Extend queue membership; False means the user is no longer queued."""
        return bool(await self._redis.expire(self.inq_key(user_id), INQ_TTL_SECONDS))
