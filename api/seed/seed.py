"""Idempotent seed: menu upsert + tables T1..T12. Run: python -m seed.seed"""

import asyncio
import json
import secrets
from pathlib import Path

import structlog
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncEngine

from app.config import get_settings
from app.db import make_engine
from app.logging import configure_logging
from app.models import DiningTable, MenuCategory, MenuItem
from app.vocab import ALLERGENS, DIETARY_TAGS

MENU_PATH = Path(__file__).parent / "menu.json"
NUM_TABLES = 12
log = structlog.get_logger("seed")


def _validate(item: dict) -> None:
    bad = set(item["dietary_tags"]) - set(DIETARY_TAGS)
    bad |= set(item["allergens"]) - set(ALLERGENS)
    if bad:
        raise ValueError(f"{item['name']}: unknown vocabulary values {sorted(bad)}")


async def seed(engine: AsyncEngine, menu_path: Path = MENU_PATH) -> dict[str, int]:
    menu = await asyncio.to_thread(lambda: json.loads(menu_path.read_text()))
    async with engine.begin() as conn:
        for cat in menu["categories"]:
            stmt = insert(MenuCategory).values(name=cat["name"], position=cat["position"])
            stmt = stmt.on_conflict_do_update(
                index_elements=["name"], set_={"position": stmt.excluded.position}
            ).returning(MenuCategory.id)
            cat_id = (await conn.execute(stmt)).scalar_one()
            for item in cat["items"]:
                _validate(item)
                values = {**item, "category_id": cat_id}
                istmt = insert(MenuItem).values(**values)
                # is_available is deliberately not overwritten: runtime state, not seed data.
                update = {k: istmt.excluded[k] for k in item}
                await conn.execute(
                    istmt.on_conflict_do_update(
                        constraint="uq_menu_items_category_name", set_=update
                    )
                )
        existing = set((await conn.execute(select(DiningTable.label))).scalars())
        for n in range(1, NUM_TABLES + 1):
            label = f"T{n}"
            if label not in existing:  # tokens are generated once, never rotated by re-seeding
                await conn.execute(
                    insert(DiningTable).values(label=label, qr_token=secrets.token_urlsafe(16))
                )
    async with engine.connect() as conn:
        counts = {}
        for model in (MenuCategory, MenuItem, DiningTable):
            rows = (await conn.execute(select(model.id))).all()
            counts[model.__tablename__] = len(rows)
    return counts


async def _main() -> None:
    configure_logging(get_settings().log_level)
    engine = make_engine()
    counts = await seed(engine)
    await engine.dispose()
    log.info("seed_complete", **counts)


if __name__ == "__main__":
    asyncio.run(_main())
