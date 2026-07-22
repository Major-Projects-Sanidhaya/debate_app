"""Client for debate-api's internal moderation intake.

Implements the INTERNAL MODERATION CONTRACT verbatim. Posting is best-effort:
one retry, then log and drop. Never raises — a moderation transport failure
must not touch the debate or the fact-check pipeline.
"""

import httpx
import structlog

logger = structlog.get_logger(__name__)

EVENTS_PATH = "/internal/moderation/events"
POST_TIMEOUT_SECONDS = 5.0


class InternalModerationClient:
    def __init__(self, base_url: str, api_key: str, *, http=None, timeout: float = POST_TIMEOUT_SECONDS):
        self._url = base_url.rstrip("/") + EVENTS_PATH
        self._api_key = api_key
        self._http = http or httpx.AsyncClient(timeout=timeout)

    async def post_event(
        self,
        *,
        match_id: str,
        source: str,
        stance: str,
        category: str,
        severity: str,
        excerpt: str,
        ts: int,
    ) -> bool:
        """POST one moderation event. Returns True on 204. Never raises."""
        body = {
            "match_id": match_id,
            "source": source,
            "stance": stance,
            "category": category,
            "severity": severity,
            "excerpt": excerpt,
            "ts": ts,
        }
        headers = {"X-Internal-Key": self._api_key}
        last_error = ""

        for attempt in (1, 2):
            try:
                response = await self._http.post(self._url, json=body, headers=headers)
                if response.status_code == 204:
                    logger.info(
                        "moderation_event_posted",
                        match_id=match_id,
                        source=source,
                        stance=stance,
                        category=category,
                        severity=severity,
                        attempt=attempt,
                    )
                    return True
                last_error = f"http {response.status_code}"
            except Exception as exc:
                last_error = repr(exc)
            logger.warning(
                "moderation_event_post_failed", attempt=attempt, error=last_error, url=self._url
            )

        logger.error(
            "moderation_event_dropped",
            match_id=match_id,
            source=source,
            stance=stance,
            category=category,
            severity=severity,
            error=last_error,
        )
        return False

    async def aclose(self) -> None:
        try:
            await self._http.aclose()
        except Exception:  # cleanup is best-effort
            pass
