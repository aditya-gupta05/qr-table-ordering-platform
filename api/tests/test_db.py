from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from seed.seed import seed
from tests.conftest import migrate, recreate_database


async def test_migration_applies_on_empty_db(pg_base):
    url = await recreate_database("qr_test_migration")
    await migrate(url)
    engine = create_async_engine(url)
    async with engine.connect() as conn:
        tables = set(
            (
                await conn.execute(
                    text("SELECT tablename FROM pg_tables WHERE schemaname='public'")
                )
            ).scalars()
        )
        ext = (await conn.execute(text("SELECT 1 FROM pg_extension WHERE extname='vector'"))).all()
        enum = (
            (
                await conn.execute(
                    text(
                        "SELECT enumlabel FROM pg_enum e JOIN pg_type t ON t.oid=e.enumtypid "
                        "WHERE t.typname='order_status' ORDER BY enumsortorder"
                    )
                )
            )
            .scalars()
            .all()
        )
    await engine.dispose()
    assert {"dining_tables", "menu_categories", "menu_items", "orders", "order_items"} <= tables
    assert ext, "pgvector extension not enabled"
    assert enum == ["received", "preparing", "ready", "served", "cancelled"]


async def test_seed_is_idempotent(migrated_test_db):
    engine = create_async_engine(migrated_test_db)
    first = await seed(engine)
    async with engine.connect() as conn:
        tokens1 = (await conn.execute(text("SELECT label, qr_token FROM dining_tables"))).all()
    second = await seed(engine)
    async with engine.connect() as conn:
        tokens2 = (await conn.execute(text("SELECT label, qr_token FROM dining_tables"))).all()
    await engine.dispose()
    assert first == second
    assert first["dining_tables"] == 12
    assert first["menu_categories"] == 6
    assert first["menu_items"] >= 30
    assert sorted(tokens1) == sorted(tokens2)
    assert all(len(tok) == 22 for _, tok in tokens1)
