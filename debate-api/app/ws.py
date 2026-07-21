import asyncio
import contextlib
import uuid
from typing import Any, Literal

import structlog
from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, ValidationError
from sqlalchemy import select

from app.auth import decode_token
from app.livekit_rooms import build_room_metadata, create_match_room
from app.livekit_tokens import mint_livekit_token
from app.matchmaking import Matchmaker, opposite, resolve_fact_check_mode
from app.models import Match, Topic, User

logger = structlog.get_logger(__name__)

router = APIRouter()

REFRESH_INTERVAL_SECONDS = 60


class JoinMessage(BaseModel):
    type: Literal["join"]
    topic_id: int
    stance: Literal["pro", "con"]
    fact_check_mode: Literal["on_demand", "auto"]


class CancelMessage(BaseModel):
    type: Literal["cancel"]


class ConnectionManager:
    """user_id -> websocket for in-process match_found delivery.

    One socket per user: a new connection replaces (and closes) the old one,
    and clears any queue membership created by it. Cleanup on disconnect is a
    no-op if the socket was already replaced, so a reconnect can't be wiped
    out by the old connection's teardown.
    """

    def __init__(self) -> None:
        self._sockets: dict[str, WebSocket] = {}
        self._refresh_tasks: dict[str, asyncio.Task] = {}

    def get(self, user_id: str) -> WebSocket | None:
        return self._sockets.get(user_id)

    async def connect(self, user_id: str, ws: WebSocket, matchmaker: Matchmaker) -> None:
        old = self._sockets.get(user_id)
        self._sockets[user_id] = ws
        if old is not None:
            self.cancel_refresh(user_id)
            await matchmaker.leave(user_id)
            with contextlib.suppress(Exception):
                await old.close(code=4000, reason="replaced by a new connection")

    async def disconnect(self, user_id: str, ws: WebSocket, matchmaker: Matchmaker) -> None:
        if self._sockets.get(user_id) is not ws:
            return
        del self._sockets[user_id]
        self.cancel_refresh(user_id)
        await matchmaker.leave(user_id)

    async def send(self, user_id: str, payload: dict[str, Any]) -> bool:
        ws = self._sockets.get(user_id)
        if ws is None:
            return False
        try:
            await ws.send_json(payload)
            return True
        except Exception:
            logger.warning("ws_send_failed", user_id=user_id, msg_type=payload.get("type"))
            return False

    def start_refresh(self, user_id: str, matchmaker: Matchmaker) -> None:
        self.cancel_refresh(user_id)
        self._refresh_tasks[user_id] = asyncio.create_task(self._refresh_loop(user_id, matchmaker))

    def cancel_refresh(self, user_id: str) -> None:
        task = self._refresh_tasks.pop(user_id, None)
        if task is not None:
            task.cancel()

    @staticmethod
    async def _refresh_loop(user_id: str, matchmaker: Matchmaker) -> None:
        while True:
            await asyncio.sleep(REFRESH_INTERVAL_SECONDS)
            if not await matchmaker.refresh(user_id):
                return


async def _error(ws: WebSocket, message: str) -> None:
    await ws.send_json({"type": "error", "message": message})


def _match_found_payload(
    state: Any,
    match_id: uuid.UUID,
    topic: Topic,
    mode: str,
    your_stance: str,
    token: str,
) -> dict[str, Any]:
    return {
        "type": "match_found",
        "match_id": str(match_id),
        "room_name": f"match_{match_id}",
        "livekit_url": state.settings.livekit_url,
        "livekit_token": token,
        "topic": {"id": topic.id, "title": topic.title},
        "your_stance": your_stance,
        "peer_stance": opposite(your_stance),
        "fact_check_mode": mode,
    }


async def _handle_join(ws: WebSocket, user_id: str, msg: JoinMessage) -> None:
    state = ws.app.state
    matchmaker: Matchmaker = state.matchmaker
    manager: ConnectionManager = state.manager

    async with state.sessionmaker() as session:
        topic = await session.scalar(
            select(Topic).where(Topic.id == msg.topic_id, Topic.active.is_(True))
        )
    if topic is None:
        await _error(ws, f"unknown or inactive topic {msg.topic_id}")
        return

    # Friendly pre-check so a double join gets an error instead of a bogus
    # "queued" ack; the Lua script re-checks atomically.
    if await matchmaker.is_queued(user_id):
        await _error(ws, "already in queue")
        return

    # Ack before the Lua script runs: once it enqueues us, a peer on another
    # task may push match_found to this socket, and "queued" must precede it.
    await ws.send_json({"type": "queued"})

    while True:
        outcome = await matchmaker.join(user_id, msg.topic_id, msg.stance, msg.fact_check_mode)
        if outcome.status == "already_queued":
            await _error(ws, "already in queue")
            return
        if outcome.status == "queued":
            manager.start_refresh(user_id, matchmaker)
            logger.info(
                "queued", user_id=user_id, topic_id=msg.topic_id, stance=msg.stance,
                fact_check_mode=msg.fact_check_mode,
            )
            return

        peer_id = outcome.peer_id
        if manager.get(peer_id) is None:
            # Peer left between enqueue and pop; its entry is consumed, try the next one.
            logger.info("skipping_gone_peer", peer_id=peer_id, topic_id=msg.topic_id)
            continue

        mode = resolve_fact_check_mode(msg.fact_check_mode, outcome.peer_mode)
        user_pro, user_con = (user_id, peer_id) if msg.stance == "pro" else (peer_id, user_id)
        async with state.sessionmaker() as session:
            match = Match(
                topic_id=topic.id,
                user_pro=uuid.UUID(user_pro),
                user_con=uuid.UUID(user_con),
                fact_check_mode=mode,
            )
            session.add(match)
            await session.commit()

        logger.info(
            "match_created",
            match_id=str(match.id),
            topic_id=topic.id,
            topic=topic.title,
            fact_check_mode=mode,
            user_pro=user_pro,
            user_con=user_con,
        )

        # Create the LiveKit room up front so debate-agent can discover the
        # match from room metadata. Best-effort: never blocks match_found.
        await create_match_room(
            state.lkapi,
            f"match_{match.id}",
            build_room_metadata(match.id, topic, mode, user_pro, user_con),
        )

        manager.cancel_refresh(peer_id)
        peer_stance = opposite(msg.stance)
        for uid, stance in ((peer_id, peer_stance), (user_id, msg.stance)):
            token = mint_livekit_token(state.settings, identity=uid, name=stance,
                                       room_name=f"match_{match.id}")
            delivered = await manager.send(
                uid, _match_found_payload(state, match.id, topic, mode, stance, token)
            )
            if not delivered:
                logger.warning("match_found_undelivered", match_id=str(match.id), user_id=uid)
        return


async def _authenticate(ws: WebSocket, token: str | None) -> "tuple[User | None, str]":
    """Returns (user, error_message); error_message is set when user is None."""
    state = ws.app.state
    user_id = decode_token(token, state.settings.jwt_secret) if token else None
    if user_id is None:
        return None, "invalid token"
    async with state.sessionmaker() as session:
        user = await session.scalar(select(User).where(User.id == user_id))
    if user is None:
        return None, "invalid token"
    if user.banned:
        return None, "account_suspended"
    return user, ""


@router.websocket("/ws/match")
async def ws_match(ws: WebSocket, token: str | None = Query(default=None)):
    # Token comes in the query string because browser WebSocket clients
    # cannot set an Authorization header.
    await ws.accept()
    user, auth_error = await _authenticate(ws, token)
    if user is None:
        await _error(ws, auth_error)
        await ws.close(code=4403 if auth_error == "account_suspended" else 4401)
        return

    user_id = str(user.id)
    state = ws.app.state
    manager: ConnectionManager = state.manager
    await manager.connect(user_id, ws, state.matchmaker)
    structlog.contextvars.bind_contextvars(user_id=user_id)
    logger.info("ws_connected")
    try:
        while True:
            try:
                raw = await ws.receive_json()
            except ValueError:
                await _error(ws, "invalid JSON")
                continue
            msg_type = raw.get("type") if isinstance(raw, dict) else None
            try:
                if msg_type == "join":
                    await _handle_join(ws, user_id, JoinMessage.model_validate(raw))
                elif msg_type == "cancel":
                    CancelMessage.model_validate(raw)
                    manager.cancel_refresh(user_id)
                    await state.matchmaker.leave(user_id)
                    logger.info("cancelled_queue")
                else:
                    await _error(ws, f"unknown message type: {msg_type!r}")
            except ValidationError as exc:
                await _error(ws, f"invalid {msg_type} message: {exc.errors()[0]['msg']}")
    except WebSocketDisconnect:
        logger.info("ws_disconnected")
    except Exception:
        logger.exception("ws_error")
    finally:
        await manager.disconnect(user_id, ws, state.matchmaker)
        structlog.contextvars.unbind_contextvars("user_id")
