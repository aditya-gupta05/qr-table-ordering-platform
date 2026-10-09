import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app import events
from app.db import get_session
from app.metrics import ORDER_STATUS_CHANGES
from app.models import DiningTable, Order
from app.routers.orders import load_order
from app.schemas import OrderOut, StatusIn
from app.vocab import ORDER_STATUSES

# NOTE: no auth on kitchen endpoints (out of scope, documented known gap).
router = APIRouter(prefix="/api/kitchen", tags=["kitchen"])
log = structlog.get_logger("kitchen")

ACTIVE = ("received", "preparing", "ready")
TRANSITIONS: dict[str, set[str]] = {
    "received": {"preparing", "cancelled"},
    "preparing": {"ready", "cancelled"},
    "ready": {"served"},
    "served": set(),
    "cancelled": set(),
}


@router.get("/orders", response_model=list[OrderOut])
async def list_orders(
    status: str | None = Query(None, description="comma-separated statuses"),
    session: AsyncSession = Depends(get_session),
):
    wanted = [s.strip() for s in status.split(",") if s.strip()] if status else list(ACTIVE)
    bad = sorted(set(wanted) - set(ORDER_STATUSES))
    if bad:
        raise HTTPException(422, detail={"message": "unknown status", "invalid": bad})
    rows = (
        await session.execute(
            select(Order, DiningTable.label)
            .join(DiningTable, DiningTable.id == Order.table_id)
            .options(selectinload(Order.items))
            .where(Order.status.in_(wanted))
            .order_by(Order.created_at, Order.id)
        )
    ).all()
    return [OrderOut.build(o, label) for o, label in rows]


@router.patch("/orders/{order_id}/status", response_model=OrderOut)
async def change_status(
    order_id: int, body: StatusIn, session: AsyncSession = Depends(get_session)
):
    if body.status not in ORDER_STATUSES:
        raise HTTPException(422, detail={"message": "unknown status", "invalid": [body.status]})
    current = (
        await session.execute(select(Order.status).where(Order.id == order_id))
    ).scalar_one_or_none()
    if current is None:
        raise HTTPException(404, "unknown order")
    if body.status not in TRANSITIONS[current]:
        raise HTTPException(409, f"cannot move order from {current} to {body.status}")

    # Compare-and-set: only one of two concurrent identical clicks matches status = current.
    changed = (
        await session.execute(
            update(Order)
            .where(Order.id == order_id, Order.status == current)
            .values(status=body.status, updated_at=func.now())
            .returning(Order.id)
        )
    ).scalar_one_or_none()
    if changed is None:
        await session.rollback()
        raise HTTPException(409, "order status changed concurrently; reload and retry")
    await session.commit()
    ORDER_STATUS_CHANGES.labels(body.status).inc()

    order = await load_order(session, Order.id == order_id)
    assert order is not None
    log.info("order_status_changed", order_id=order_id, from_status=current, to=body.status)
    await events.publish(events.CHANNEL_STATUS, "order.status_changed", order)
    return order
