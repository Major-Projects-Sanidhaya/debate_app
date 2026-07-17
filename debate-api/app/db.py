from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine


def make_engine_and_sessionmaker(postgres_url: str):
    engine = create_async_engine(postgres_url, pool_pre_ping=True)
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    return engine, sessionmaker
