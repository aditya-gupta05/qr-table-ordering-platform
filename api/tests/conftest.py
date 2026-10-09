"""Tests run on the host against the compose Postgres/Redis (55432/56379).

They use a dedicated `qr_test` database, so dev data in `qr` is never touched.
Env must be set before `app` is imported (engine is created at import time).
"""

import os

PG = os.environ.get("TEST_PG_BASE", "postgresql+asyncpg://qr:qr@localhost:55432")
os.environ["DATABASE_URL"] = f"{PG}/qr_test"
os.environ["REDIS_URL"] = os.environ.get("TEST_REDIS_URL", "redis://localhost:56379/15")

import asyncio  # noqa: E402
from pathlib import Path  # noqa: E402

import httpx  # noqa: E402
import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402
from alembic.config import Config  # noqa: E402
from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import create_async_engine  # noqa: E402

from alembic import command  # noqa: E402

API_DIR = Path(__file__).resolve().parent.parent


async def recreate_database(name: str) -> str:
    """Drop and create database `name` via the admin `postgres` DB; returns its URL."""
    admin = create_async_engine(f"{PG}/postgres", isolation_level="AUTOCOMMIT")
    async with admin.connect() as conn:
        await conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        await conn.execute(text(f'CREATE DATABASE "{name}"'))
    await admin.dispose()
    return f"{PG}/{name}"


async def migrate(url: str) -> None:
    os.environ["ALEMBIC_DATABASE_URL"] = url
    cfg = Config(str(API_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(API_DIR / "alembic"))
    # env.py calls asyncio.run, so it must run off the test event loop.
    await asyncio.to_thread(command.upgrade, cfg, "head")


@pytest_asyncio.fixture(scope="session", autouse=True)
async def migrated_test_db():
    url = await recreate_database("qr_test")
    await migrate(url)
    yield url


@pytest_asyncio.fixture
async def client():
    from app.main import app

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture
def pg_base() -> str:
    return PG


@pytest_asyncio.fixture(scope="session", autouse=True)
async def seeded_test_db(migrated_test_db):
    from seed.seed import seed

    engine = create_async_engine(migrated_test_db)
    await seed(engine)
    await engine.dispose()
    yield migrated_test_db
