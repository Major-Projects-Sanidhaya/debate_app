from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user, get_session
from app.models import Topic, User

router = APIRouter()


class TopicOut(BaseModel):
    id: int
    title: str


@router.get("/topics", response_model=list[TopicOut])
async def list_topics(
    session: AsyncSession = Depends(get_session), _user: User = Depends(get_current_user)
) -> list[TopicOut]:
    rows = await session.scalars(
        select(Topic).where(Topic.active.is_(True)).order_by(Topic.id)
    )
    return [TopicOut(id=t.id, title=t.title) for t in rows]
