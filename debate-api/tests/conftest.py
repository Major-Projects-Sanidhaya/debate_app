"""Test setup: real Postgres + Redis via testcontainers (Docker required).

The Alembic migration and the seed script run once per session against the
Postgres container, so both are exercised by the suite. Env var POSTGRES_URL
is pointed at the container *before* app settings are cached, so nothing can
touch a developer database.
"""

import asyncio
import os
import uuid
from pathlib import Path

import pytest
import redis as sync_redis
from sqlalchemy import text
from starlette.testclient import TestClient
from testcontainers.postgres import PostgresContainer
from testcontainers.redis import RedisContainer

REPO_ROOT = Path(__file__).resolve().parent.parent

TEST_LIVEKIT_KEY = "devkey"
TEST_LIVEKIT_SECRET = "devsecret_change_me_padded_to_32_chars_for_tests"
TEST_JWT_SECRET = "test-jwt-secret-padded-out-to-32-bytes"


@pytest.fixture(scope="session")
def pg_url():
    with PostgresContainer(
        "postgres:16-alpine", username="debate", password="debate", dbname="debate"
    ) as pg:
        host = pg.get_container_host_ip()
        port = pg.get_exposed_port(5432)
        yield f"postgresql+asyncpg://debate:debate@{host}:{port}/debate"


@pytest.fixture(scope="session")
def redis_url():
    with RedisContainer("redis:7-alpine") as rc:
        yield f"redis://{rc.get_container_host_ip()}:{rc.get_exposed_port(6379)}/0"


@pytest.fixture(scope="session")
def migrated(pg_url):
    os.environ["POSTGRES_URL"] = pg_url
    from app.config import get_settings

    get_settings.cache_clear()

    from alembic.config import Config

    from alembic import command

    cfg = Config(str(REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(REPO_ROOT / "alembic"))
    command.upgrade(cfg, "head")
    return pg_url


@pytest.fixture(scope="session")
def topic_ids(migrated) -> list[int]:
    from scripts.seed import main as seed_main

    asyncio.run(seed_main())

    async def fetch() -> list[int]:
        from app.db import make_engine_and_sessionmaker

        engine, sessionmaker = make_engine_and_sessionmaker(migrated)
        async with sessionmaker() as session:
            rows = await session.execute(text("SELECT id FROM topics ORDER BY id"))
            ids = [r[0] for r in rows]
        await engine.dispose()
        return ids

    return asyncio.run(fetch())


def run_sql(pg_url: str, statement: str, params: dict | None = None) -> None:
    async def _run():
        from app.db import make_engine_and_sessionmaker

        engine, sessionmaker = make_engine_and_sessionmaker(pg_url)
        async with sessionmaker() as session:
            await session.execute(text(statement), params or {})
            await session.commit()
        await engine.dispose()

    asyncio.run(_run())


@pytest.fixture
def clean(migrated, redis_url):
    """Fresh users/matches tables and empty Redis before each test; topics stay seeded."""
    run_sql(migrated, "TRUNCATE users CASCADE")
    r = sync_redis.from_url(redis_url)
    r.flushdb()
    r.close()


@pytest.fixture
def settings(migrated, redis_url, clean):
    from app.config import Settings

    return Settings(
        _env_file=None,
        postgres_url=migrated,
        redis_url=redis_url,
        livekit_url="ws://livekit.test:7880",
        livekit_api_key=TEST_LIVEKIT_KEY,
        livekit_api_secret=TEST_LIVEKIT_SECRET,
        jwt_secret=TEST_JWT_SECRET,
        cors_origins="*",
    )


@pytest.fixture
def client(settings, topic_ids):
    from app.main import create_app

    with TestClient(create_app(settings)) as c:
        yield c


@pytest.fixture
def sync_redis_client(redis_url):
    r = sync_redis.from_url(redis_url, decode_responses=True)
    yield r
    r.close()


def auth_device(client: TestClient, device_id: str | None = None) -> dict:
    resp = client.post(
        "/auth/device", json={"device_id": device_id or str(uuid.uuid4()), "over_18": True}
    )
    assert resp.status_code == 201, resp.text
    return resp.json()
