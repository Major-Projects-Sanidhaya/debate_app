"""72h Redis cache of verified claims, keyed by normalized-claim sha256."""

import json

import structlog

from pipeline.normalize import claim_hash
from pipeline.models import Verdict

logger = structlog.get_logger(__name__)

CACHE_TTL_SECONDS = 72 * 3600


class ClaimCache:
    def __init__(self, redis):
        self._redis = redis

    @staticmethod
    def key(claim: str) -> str:
        return f"claim_cache:{claim_hash(claim)}"

    async def get(self, claim: str) -> "dict | None":
        raw = await self._redis.get(self.key(claim))
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            logger.warning("claim_cache_corrupt", key=self.key(claim))
            return None

    async def put(self, claim: str, verdict: Verdict) -> bool:
        """Cache the verdict; "unverifiable" is never cached (evidence may emerge)."""
        if verdict.verdict == "unverifiable":
            return False
        await self._redis.set(
            self.key(claim), json.dumps(verdict.core_dict()), ex=CACHE_TTL_SECONDS
        )
        return True
