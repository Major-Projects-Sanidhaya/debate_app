import time
import uuid
from contextlib import asynccontextmanager

import redis.asyncio as aioredis
import structlog
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from prometheus_fastapi_instrumentator import Instrumentator

from app.config import Settings, get_settings
from app.db import make_engine_and_sessionmaker
from app.livekit_rooms import make_livekit_api
from app.logging_config import configure_logging
from app.matchmaking import Matchmaker
from app.routes import auth_routes, health, matches, topics
from app.ws import ConnectionManager
from app.ws import router as ws_router

logger = structlog.get_logger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    configure_logging()
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        engine, sessionmaker = make_engine_and_sessionmaker(settings.postgres_url)
        redis = aioredis.from_url(settings.redis_url, decode_responses=True)
        lkapi = make_livekit_api(settings)
        app.state.settings = settings
        app.state.engine = engine
        app.state.sessionmaker = sessionmaker
        app.state.redis = redis
        app.state.matchmaker = Matchmaker(redis)
        app.state.manager = ConnectionManager()
        app.state.lkapi = lkapi
        logger.info("startup")
        yield
        await lkapi.aclose()
        await redis.aclose()
        await engine.dispose()
        logger.info("shutdown")

    app = FastAPI(title="debate-api", lifespan=lifespan)

    origins = settings.cors_origin_list
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials="*" not in origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        structlog.contextvars.clear_contextvars()
        request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        structlog.contextvars.bind_contextvars(request_id=request_id)
        start = time.perf_counter()
        response = await call_next(request)
        if request.url.path not in ("/metrics", "/healthz"):
            logger.info(
                "request",
                method=request.method,
                path=request.url.path,
                status=response.status_code,
                duration_ms=round((time.perf_counter() - start) * 1000, 1),
            )
        response.headers["X-Request-ID"] = request_id
        return response

    Instrumentator().instrument(app).expose(app, include_in_schema=False)

    app.include_router(health.router)
    app.include_router(auth_routes.router)
    app.include_router(topics.router)
    app.include_router(matches.router)
    app.include_router(ws_router)
    return app


app = create_app()
