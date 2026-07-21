"""User reports, blocks, and the internal moderation-event intake.

Side-effect policy:
- underage / sexual_content reports and severity="severe" internal events end
  the match: LiveKit room deleted (best-effort), matches.ended_reason set to
  'moderation', offending user flagged.
- >=3 distinct reporters against one user within 24h auto-bans them.
- Blocks write one-directional Redis sets (blocks:{blocker}); the matchmaking
  Lua script checks both directions at match time.
"""

import uuid
from datetime import UTC, datetime
from typing import Literal

import structlog
from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import distinct, func, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user, get_session
from app.livekit_rooms import delete_match_room
from app.models import Block, Match, ModerationEvent, Report, User

logger = structlog.get_logger(__name__)

router = APIRouter()

AUTO_BAN_DISTINCT_REPORTERS = 3
IMMEDIATE_ACTION_REASONS = ("underage", "sexual_content")

ReportReason = Literal[
    "harassment", "hate_speech", "sexual_content", "violence_threat", "underage", "spam_other"
]
EventSource = Literal["transcript", "video"]
EventStance = Literal["pro", "con"]
EventCategory = Literal[
    "harassment_hate", "sexual_content", "minor_safety", "violence_threat", "self_harm", "test"
]
EventSeverity = Literal["medium", "severe"]


class ReportIn(BaseModel):
    reason: ReportReason
    details: str | None = Field(default=None, max_length=500)


class ModerationEventIn(BaseModel):
    match_id: uuid.UUID
    source: EventSource
    stance: EventStance
    category: EventCategory
    severity: EventSeverity
    excerpt: str = Field(max_length=300)
    ts: float


async def _load_match_for_participant(
    session: AsyncSession, match_id: uuid.UUID, user: User
) -> Match:
    match = await session.scalar(select(Match).where(Match.id == match_id))
    if match is None:
        raise HTTPException(status_code=404, detail="match not found")
    if user.id not in (match.user_pro, match.user_con):
        raise HTTPException(status_code=403, detail="not a participant of this match")
    return match


async def _end_match_for_moderation(
    request: Request, session: AsyncSession, match: Match, flagged_user_id: uuid.UUID
) -> None:
    """Kill the room, mark the match, flag the user. Room failures never raise."""
    await delete_match_room(request.app.state.lkapi, f"match_{match.id}")
    await session.execute(
        update(Match)
        .where(Match.id == match.id)
        .values(
            ended_reason="moderation",
            ended_at=func.coalesce(Match.ended_at, func.now()),
        )
    )
    await session.execute(
        update(User).where(User.id == flagged_user_id).values(flagged=True)
    )


async def _maybe_auto_ban(session: AsyncSession, reported_id: uuid.UUID) -> None:
    distinct_reporters = await session.scalar(
        select(func.count(distinct(Report.reporter_id))).where(
            Report.reported_id == reported_id,
            Report.created_at >= func.now() - text("interval '24 hours'"),
        )
    )
    if distinct_reporters is None or distinct_reporters < AUTO_BAN_DISTINCT_REPORTERS:
        return
    result = await session.execute(
        update(User)
        .where(User.id == reported_id, User.banned.is_(False))
        .values(banned=True)
    )
    if result.rowcount:
        logger.error(
            "auto_ban_triggered",
            user_id=str(reported_id),
            distinct_reporters=distinct_reporters,
            window_hours=24,
        )


@router.post("/matches/{match_id}/report", status_code=204)
async def report_match(
    match_id: uuid.UUID,
    body: ReportIn,
    request: Request,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
) -> Response:
    match = await _load_match_for_participant(session, match_id, user)
    reported_id = match.user_con if user.id == match.user_pro else match.user_pro

    # Idempotent per (reporter, match): the unique constraint absorbs repeats.
    result = await session.execute(
        pg_insert(Report)
        .values(
            id=uuid.uuid4(),
            match_id=match.id,
            reporter_id=user.id,
            reported_id=reported_id,
            reason=body.reason,
            details=body.details,
        )
        .on_conflict_do_nothing(constraint="uq_reports_reporter_match")
    )
    created = bool(result.rowcount)

    # Immediate-action reasons run their side effects even on a repeat report
    # (the first report may have carried a milder reason).
    if body.reason in IMMEDIATE_ACTION_REASONS:
        await _end_match_for_moderation(request, session, match, reported_id)

    await _maybe_auto_ban(session, reported_id)
    await session.commit()

    logger.info(
        "report_received",
        match_id=str(match.id),
        reporter_id=str(user.id),
        reported_id=str(reported_id),
        reason=body.reason,
        created=created,
    )
    return Response(status_code=204)


@router.post("/matches/{match_id}/block", status_code=204)
async def block_opponent(
    match_id: uuid.UUID,
    request: Request,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
) -> Response:
    match = await _load_match_for_participant(session, match_id, user)
    blocked_id = match.user_con if user.id == match.user_pro else match.user_pro

    await session.execute(
        pg_insert(Block)
        .values(blocker_id=user.id, blocked_id=blocked_id)
        .on_conflict_do_nothing(index_elements=["blocker_id", "blocked_id"])
    )
    await session.commit()

    # One-directional set; the matchmaking Lua checks both directions.
    await request.app.state.redis.sadd(f"blocks:{user.id}", str(blocked_id))

    logger.info(
        "block_created", blocker_id=str(user.id), blocked_id=str(blocked_id),
        match_id=str(match.id),
    )
    return Response(status_code=204)


@router.post("/internal/moderation/events", status_code=204)
async def internal_moderation_event(
    body: ModerationEventIn,
    request: Request,
    session: AsyncSession = Depends(get_session),
    x_internal_key: str | None = Header(default=None),
) -> Response:
    if x_internal_key != request.app.state.settings.internal_api_key:
        raise HTTPException(status_code=401, detail="invalid internal key")

    match = await session.scalar(select(Match).where(Match.id == body.match_id))
    if match is None:
        raise HTTPException(status_code=404, detail="match not found")

    offender_id = match.user_pro if body.stance == "pro" else match.user_con
    session.add(
        ModerationEvent(
            match_id=match.id,
            source=body.source,
            stance=body.stance,
            user_id=offender_id,
            category=body.category,
            severity=body.severity,
            excerpt=body.excerpt,
            created_at=datetime.fromtimestamp(body.ts, tz=UTC),
        )
    )

    if body.severity == "severe":
        await _end_match_for_moderation(request, session, match, offender_id)

    await session.commit()

    logger.info(
        "moderation_event_received",
        match_id=str(match.id),
        source=body.source,
        stance=body.stance,
        user_id=str(offender_id),
        category=body.category,
        severity=body.severity,
    )
    return Response(status_code=204)
