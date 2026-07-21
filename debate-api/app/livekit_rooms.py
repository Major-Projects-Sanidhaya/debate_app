"""Explicit LiveKit room creation at match time.

The room is created before match_found goes out so debate-agent can join as a
hidden participant and read match context from the room metadata (see the
ROOM METADATA CONTRACT in the README). Room creation is best-effort by design:
users can debate without an agent, so failures are logged, never raised.
"""

import json
import uuid

import structlog
from livekit import api

from app.config import Settings
from app.models import Topic

logger = structlog.get_logger(__name__)

EMPTY_TIMEOUT_SECONDS = 300


def http_url(ws_url: str) -> str:
    """RoomService is Twirp-over-HTTP; the env var carries the ws:// URL."""
    if ws_url.startswith("ws://"):
        return "http://" + ws_url.removeprefix("ws://")
    if ws_url.startswith("wss://"):
        return "https://" + ws_url.removeprefix("wss://")
    return ws_url


def make_livekit_api(settings: Settings) -> api.LiveKitAPI:
    return api.LiveKitAPI(
        url=http_url(settings.livekit_url),
        api_key=settings.livekit_api_key,
        api_secret=settings.livekit_api_secret,
    )


def build_room_metadata(
    match_id: uuid.UUID, topic: Topic, fact_check_mode: str, user_pro: str, user_con: str
) -> str:
    # Exact shape of the ROOM METADATA CONTRACT — debate-agent parses this
    # verbatim; do not rename keys.
    return json.dumps(
        {
            "match_id": str(match_id),
            "topic_id": topic.id,
            "topic": topic.title,
            "fact_check_mode": fact_check_mode,
            "user_pro": user_pro,
            "user_con": user_con,
        }
    )


async def delete_match_room(lkapi: api.LiveKitAPI, room_name: str) -> None:
    """Moderation kill-switch: drop the room so both clients disconnect.
    Best-effort — failures are logged, never raised."""
    try:
        await lkapi.room.delete_room(api.DeleteRoomRequest(room=room_name))
        logger.info("livekit_room_deleted", room=room_name)
    except Exception:
        logger.exception("livekit_room_delete_failed", room=room_name)


async def create_match_room(lkapi: api.LiveKitAPI, room_name: str, metadata: str) -> None:
    """Create the match room with metadata; never raises."""
    try:
        existing = await lkapi.room.list_rooms(api.ListRoomsRequest(names=[room_name]))
        if existing.rooms:
            logger.warning("livekit_room_already_exists", room=room_name)
            return
        await lkapi.room.create_room(
            api.CreateRoomRequest(
                name=room_name,
                empty_timeout=EMPTY_TIMEOUT_SECONDS,
                metadata=metadata,
            )
        )
        logger.info("livekit_room_created", room=room_name)
    except Exception:
        # Matching must survive LiveKit API trouble; the debate itself can
        # proceed without an agent-readable room.
        logger.exception("livekit_room_create_failed", room=room_name)
