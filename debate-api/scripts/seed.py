"""Seed the topics table. Idempotent. Run: python -m scripts.seed"""

import asyncio

from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.config import get_settings
from app.db import make_engine_and_sessionmaker
from app.models import Topic

TOPICS = [
    "Gun control",
    "Abortion access",
    "Universal healthcare",
    "Immigration policy",
    "Climate change regulation",
    "Minimum wage increase",
    "Death penalty",
    "Marijuana legalization",
    "Electoral college",
    "Student loan forgiveness",
]


async def main() -> None:
    engine, sessionmaker = make_engine_and_sessionmaker(get_settings().postgres_url)
    async with sessionmaker() as session:
        result = await session.execute(
            pg_insert(Topic)
            .values([{"title": t} for t in TOPICS])
            .on_conflict_do_nothing(index_elements=["title"])
        )
        await session.commit()
    await engine.dispose()
    inserted = result.rowcount
    print(f"seeded topics: {inserted} inserted, {len(TOPICS) - inserted} already present")


if __name__ == "__main__":
    asyncio.run(main())
