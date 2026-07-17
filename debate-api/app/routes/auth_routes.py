import structlog
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_session, mint_token
from app.models import User

logger = structlog.get_logger(__name__)

router = APIRouter()


class DeviceAuthIn(BaseModel):
    device_id: str = Field(min_length=1, max_length=255)
    over_18: bool


class DeviceAuthOut(BaseModel):
    token: str
    user_id: str


@router.post("/auth/device", response_model=DeviceAuthOut, status_code=201)
async def auth_device(
    body: DeviceAuthIn, request: Request, session: AsyncSession = Depends(get_session)
) -> DeviceAuthOut:
    if body.over_18 is not True:
        raise HTTPException(status_code=403, detail="must be over 18")

    # Idempotent per device_id, race-safe across replicas.
    result = await session.execute(
        pg_insert(User)
        .values(device_id=body.device_id)
        .on_conflict_do_nothing(index_elements=["device_id"])
    )
    await session.commit()
    user = await session.scalar(select(User).where(User.device_id == body.device_id))
    if user.banned:
        raise HTTPException(status_code=403, detail="user is banned")

    logger.info("device_authed", user_id=str(user.id), created=result.rowcount == 1)
    token = mint_token(user.id, request.app.state.settings.jwt_secret)
    return DeviceAuthOut(token=token, user_id=str(user.id))
