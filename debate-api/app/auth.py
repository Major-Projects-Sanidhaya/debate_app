import uuid
from datetime import UTC, datetime, timedelta

import jwt
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User

TOKEN_TTL = timedelta(days=30)

_bearer = HTTPBearer(auto_error=False)


def mint_token(user_id: uuid.UUID, secret: str) -> str:
    now = datetime.now(UTC)
    payload = {"sub": str(user_id), "iat": now, "exp": now + TOKEN_TTL}
    return jwt.encode(payload, secret, algorithm="HS256")


def decode_token(token: str, secret: str) -> uuid.UUID | None:
    """Returns the user id, or None for any invalid/expired/malformed token."""
    try:
        payload = jwt.decode(token, secret, algorithms=["HS256"])
        return uuid.UUID(payload["sub"])
    except (jwt.PyJWTError, KeyError, ValueError):
        return None


async def get_session(request: Request):
    async with request.app.state.sessionmaker() as session:
        yield session


async def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    session: AsyncSession = Depends(get_session),
) -> User:
    if credentials is None:
        raise HTTPException(status_code=401, detail="missing bearer token")
    user_id = decode_token(credentials.credentials, request.app.state.settings.jwt_secret)
    if user_id is None:
        raise HTTPException(status_code=401, detail="invalid or expired token")
    user = await session.scalar(select(User).where(User.id == user_id))
    if user is None:
        raise HTTPException(status_code=401, detail="unknown user")
    if user.banned:
        raise HTTPException(status_code=403, detail="user is banned")
    return user
