from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import get_settings


def make_engine(url: str | None = None) -> AsyncEngine:
    cfg = get_settings()
    # No pool_pre_ping: it costs a round trip on every checkout. Stale connections are bounded by
    # pool_recycle instead; see the "workers and pool sizing" section of the phase 3 report.
    return create_async_engine(
        url or cfg.database_url,
        pool_size=cfg.db_pool_size,
        max_overflow=cfg.db_max_overflow,
        pool_recycle=cfg.db_pool_recycle,
    )


engine = make_engine()
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def get_session():
    async with SessionLocal() as session:
        yield session
