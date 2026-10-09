import hashlib
import json
import time
import uuid

import structlog
from fastapi import APIRouter, Depends, Header, HTTPException, Response
from sqlalchemy import insert as core_insert
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app import events
from app.db import get_session
from app.metrics import ORDER_CREATE_SECONDS, ORDERS_CREATED
from app.models import DiningTable, MenuItem, Order, OrderItem
from app.schemas import OrderIn, OrderOut

router = APIRouter(prefix="/api", tags=["orders"])
log = structlog.get_logger("orders")


def request_hash(body: OrderIn) -> str:
    """sha256 over canonical JSON so key reuse with a different payload is detectable."""
    canonical = json.dumps(body.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


async def load_order(session: AsyncSession, *where) -> OrderOut | None:
    row = (
        await session.execute(
            select(Order, DiningTable.label)
            .join(DiningTable, DiningTable.id == Order.table_id)
            .options(selectinload(Order.items))
            .where(*where)
            .execution_options(populate_existing=True)
        )
    ).first()
    return OrderOut.build(row[0], row[1]) if row else None


@router.post("/orders", response_model=OrderOut, status_code=201)
async def create_order(
    body: OrderIn,
    response: Response,
    idempotency_key: str | None = Header(None),
    session: AsyncSession = Depends(get_session),
):
    if idempotency_key is None or not 8 <= len(idempotency_key) <= 128:
        raise HTTPException(400, "Idempotency-Key header required (8-128 characters)")
    start = time.perf_counter()

    table_id = (
        await session.execute(
            select(DiningTable.id).where(DiningTable.qr_token == body.table_token)
        )
    ).scalar_one_or_none()
    if table_id is None:
        raise HTTPException(404, "unknown table")

    # Server-side pricing: prices come from the DB, never from the client.
    wanted = {line.menu_item_id for line in body.items}
    menu = {
        m.id: m
        for m in (await session.execute(select(MenuItem).where(MenuItem.id.in_(wanted)))).scalars()
    }
    missing = sorted(wanted - menu.keys())
    unavailable = sorted(i for i, m in menu.items() if not m.is_available)
    if missing or unavailable:
        raise HTTPException(
            422,
            detail={
                "message": "some items cannot be ordered",
                "missing_item_ids": missing,
                "unavailable_item_ids": unavailable,
            },
        )
    total = sum(menu[line.menu_item_id].price_cents * line.qty for line in body.items)

    # Concurrency-safe idempotency: the UNIQUE index arbitrates. A concurrent duplicate blocks
    # on the index until the first transaction commits, then DO NOTHING returns no row.
    req_hash = request_hash(body)
    inserted = (
        await session.execute(
            pg_insert(Order)
            .values(
                table_id=table_id,
                total_cents=total,
                idempotency_key=idempotency_key,
                request_hash=req_hash,
                notes=body.notes,
            )
            .on_conflict_do_nothing(index_elements=["idempotency_key"])
            .returning(Order.id)
        )
    ).scalar_one_or_none()

    if inserted is None:
        existing = await session.execute(
            select(Order.id, Order.request_hash).where(Order.idempotency_key == idempotency_key)
        )
        row = existing.one()
        if row.request_hash != req_hash:
            raise HTTPException(409, "Idempotency-Key was already used with a different request")
        order = await load_order(session, Order.id == row.id)
        response.status_code = 200
        response.headers["Idempotent-Replay"] = "true"
        return order

    await session.execute(
        core_insert(OrderItem),
        [
            {
                "order_id": inserted,
                "menu_item_id": line.menu_item_id,
                "qty": line.qty,
                "unit_price_cents": menu[line.menu_item_id].price_cents,
                "item_name": menu[line.menu_item_id].name,
                "notes": line.notes,
            }
            for line in body.items
        ],
    )
    await session.commit()
    ORDER_CREATE_SECONDS.observe(time.perf_counter() - start)
    ORDERS_CREATED.inc()

    order = await load_order(session, Order.id == inserted)
    assert order is not None
    log.info("order_created", order_id=order.id, table=order.table_label, total_cents=total)
    # Publish strictly after commit; failure is logged/counted inside and never raised.
    await events.publish(events.CHANNEL_NEW, "order.created", order)
    return order


@router.get("/orders/{public_id}", response_model=OrderOut)
async def get_order(public_id: uuid.UUID, session: AsyncSession = Depends(get_session)):
    order = await load_order(session, Order.public_id == public_id)
    if order is None:
        raise HTTPException(404, "unknown order")
    return order
