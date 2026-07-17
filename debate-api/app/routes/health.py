import structlog
from fastapi import APIRouter, Request, Response
from sqlalchemy import text

logger = structlog.get_logger(__name__)

router = APIRouter()


@router.get("/healthz")
async def healthz(request: Request, response: Response) -> dict[str, str]:
    state = request.app.state
    try:
        async with state.sessionmaker() as session:
            await session.execute(text("SELECT 1"))
        await state.redis.ping()
    except Exception:
        logger.exception("healthz_failed")
        response.status_code = 503
        return {"status": "error"}
    return {"status": "ok"}
