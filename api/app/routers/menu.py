from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models import DiningTable, MenuCategory, MenuItem
from app.schemas import MenuCategoryOut, MenuItemOut, TableOut
from app.vocab import ALLERGENS, DIETARY_TAGS

router = APIRouter(prefix="/api", tags=["menu"])


def parse_csv(raw: str | None, allowed: tuple[str, ...], name: str) -> list[str]:
    if not raw:
        return []
    values = [v.strip() for v in raw.split(",") if v.strip()]
    bad = sorted(set(values) - set(allowed))
    if bad:
        raise HTTPException(
            422, detail={"message": f"unknown {name} values", "invalid": bad, "allowed": allowed}
        )
    return values


@router.get("/tables/{qr_token}", response_model=TableOut)
async def get_table(qr_token: str, session: AsyncSession = Depends(get_session)):
    row = (
        await session.execute(
            select(DiningTable.id, DiningTable.label).where(DiningTable.qr_token == qr_token)
        )
    ).first()
    if row is None:
        raise HTTPException(404, "unknown table")
    return TableOut(id=row.id, label=row.label)


@router.get("/menu", response_model=list[MenuCategoryOut])
async def get_menu(
    exclude_allergens: str | None = Query(None, description="comma-separated allergens to avoid"),
    dietary: str | None = Query(None, description="comma-separated tags; item must have all"),
    session: AsyncSession = Depends(get_session),
):
    avoid = parse_csv(exclude_allergens, ALLERGENS, "allergen")
    need = parse_csv(dietary, DIETARY_TAGS, "dietary")

    stmt = (
        select(MenuItem)
        .join(MenuCategory)
        .where(MenuItem.is_available.is_(True))
        .order_by(MenuCategory.position, MenuItem.id)
    )
    if avoid:
        stmt = stmt.where(~MenuItem.allergens.overlap(avoid))  # && : shares any element
    if need:
        stmt = stmt.where(MenuItem.dietary_tags.contains(need))  # @> : has all elements
    items = (await session.execute(stmt)).scalars().all()
    cats = {
        c.id: c
        for c in (
            await session.execute(select(MenuCategory).order_by(MenuCategory.position))
        ).scalars()
    }
    grouped: dict[int, list[MenuItemOut]] = {}
    for it in items:
        grouped.setdefault(it.category_id, []).append(MenuItemOut.model_validate(it))
    return [
        MenuCategoryOut(id=c.id, name=c.name, items=grouped[c.id])
        for c in cats.values()
        if c.id in grouped
    ]


@router.get("/menu/items/{item_id}", response_model=MenuItemOut)
async def get_menu_item(item_id: int, session: AsyncSession = Depends(get_session)):
    item = await session.get(MenuItem, item_id)
    if item is None:
        raise HTTPException(404, "unknown menu item")
    return MenuItemOut.model_validate(item)
