import uuid

import structlog
from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user, get_session
from app.models import Match, User

logger = structlog.get_logger(__name__)

router = APIRouter()


@router.post("/matches/{match_id}/end", status_code=204)
async def end_match(
    match_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
) -> Response:
    match = await session.scalar(select(Match).where(Match.id == match_id))
    if match is None:
        raise HTTPException(status_code=404, detail="match not found")
    if user.id not in (match.user_pro, match.user_con):
        raise HTTPException(status_code=403, detail="not a participant of this match")

    if match.ended_at is None:
        await session.execute(
            update(Match)
            .where(Match.id == match_id, Match.ended_at.is_(None))
            .values(ended_at=func.now())
        )
        await session.commit()
        logger.info("match_ended", match_id=str(match_id), ended_by=str(user.id))
    return Response(status_code=204)
