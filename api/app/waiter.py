"""Waiter requests: schema, service functions and event publishing.

Transaction contract (for the chat action endpoint, WP3):
  * `create_waiter_request(session, ...)` runs on the CALLER's AsyncSession and does NOT commit.
    The caller commits its own transaction (for example together with the chat_actions status
    update), then calls `announce(wr, created)`.
  * `announce` publishes `waiter.requested` only when `created` is True, strictly after the commit
    (AGENTS.md: publish after commit), increments waiter_requests_total{source} and logs.
    It never raises.
"""

import uuid
from datetime import datetime
from typing import Annotated, Literal

import structlog
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app import events
from app.chat.metrics import WAITER_REQUESTS
from app.models import DiningTable, WaiterRequest

log = structlog.get_logger("waiter")

Reason = Literal["allergy_or_dietary", "question_not_answered", "diner_asked_for_staff", "other"]
Status = Literal["open", "acknowledged", "closed"]
Source = Literal["chat", "button"]

# open -> acknowledged; open|acknowledged -> closed. Anything else is a 409.
TRANSITIONS: dict[str, set[str]] = {
    "open": {"acknowledged", "closed"},
    "acknowledged": {"closed"},
    "closed": set(),
}


class WaiterRequestIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    table_token: str
    reason: Reason
    note: Annotated[str | None, Field(max_length=200)] = None
    session_id: uuid.UUID | None = None


class WaiterPatchIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["acknowledged", "closed"]


class WaiterRequestOut(BaseModel):
    id: int
    table_label: str
    reason: str
    note: str | None
    source: str
    status: str
    session_id: uuid.UUID | None
    created_at: datetime
    updated_at: datetime


def to_out(wr: WaiterRequest, label: str) -> WaiterRequestOut:
    return WaiterRequestOut(
        id=wr.id,
        table_label=label,
        reason=wr.reason,
        note=wr.note,
        source=wr.source,
        status=wr.status,
        session_id=wr.session_id,
        created_at=wr.created_at,
        updated_at=wr.updated_at,
    )


async def load_waiter_request(session: AsyncSession, *where) -> WaiterRequestOut | None:
    row = (
        await session.execute(
            select(WaiterRequest, DiningTable.label)
            .join(DiningTable, DiningTable.id == WaiterRequest.table_id)
            .where(*where)
            .execution_options(populate_existing=True)
        )
    ).first()
    return to_out(row[0], row[1]) if row else None


async def create_waiter_request(
    session: AsyncSession,
    table_id: int,
    reason: str,
    note: str | None,
    source: str,
    session_id: uuid.UUID | None = None,
    action_id: uuid.UUID | None = None,
) -> tuple[WaiterRequestOut, bool]:
    """Insert a request or return the existing one. Returns (row, created). Does NOT commit.

    Race safety: INSERT .. ON CONFLICT DO NOTHING (no target, so both the partial unique index
    "one open request per table" and the unique action_id arbitrate). A concurrent inserter blocks
    on the index until the first transaction commits, then gets no row back and selects the winner.
    """
    for _ in range(2):  # 2nd pass only if the open row was closed/acknowledged between steps
        inserted = (
            await session.execute(
                pg_insert(WaiterRequest)
                .values(
                    table_id=table_id,
                    reason=reason,
                    note=note,
                    source=source,
                    session_id=session_id,
                    action_id=action_id,
                )
                .on_conflict_do_nothing()
                .returning(WaiterRequest.id)
            )
        ).scalar_one_or_none()
        if inserted is not None:
            wr = await load_waiter_request(session, WaiterRequest.id == inserted)
            assert wr is not None
            return wr, True
        # Same action replayed (even if its request is closed now): return that request.
        if action_id is not None:
            existing = await load_waiter_request(session, WaiterRequest.action_id == action_id)
            if existing is not None:
                return existing, False
        existing = await load_waiter_request(
            session, WaiterRequest.table_id == table_id, WaiterRequest.status == "open"
        )
        if existing is not None:
            return existing, False
    raise RuntimeError("waiter request insert conflicted but no conflicting row found")


async def announce(wr: WaiterRequestOut, created: bool = True) -> None:
    """Call AFTER the transaction committed. `created=False` is a no-op (existing request)."""
    if not created:
        return
    WAITER_REQUESTS.labels(wr.source).inc()
    log.info(
        "waiter_request_created",
        waiter_request_id=wr.id,
        table=wr.table_label,
        reason=wr.reason,
        source=wr.source,
    )
    await publish(wr, "waiter.requested")


async def publish(wr: WaiterRequestOut, event_type: str) -> bool:
    return await events.publish_envelope(
        events.CHANNEL_STAFF,
        event_type,
        "waiter_request",
        wr.model_dump(mode="json"),
        waiter_request_id=wr.id,
    )


async def change_status(
    session: AsyncSession, request_id: int, new_status: str
) -> tuple[WaiterRequestOut | None, str | None]:
    """Compare-and-set transition. Returns (row, error) with error in
    {'unknown','invalid'} or None. Does NOT commit (caller commits, then publishes)."""
    current = (
        await session.execute(select(WaiterRequest.status).where(WaiterRequest.id == request_id))
    ).scalar_one_or_none()
    if current is None:
        return None, "unknown"
    if new_status not in TRANSITIONS[current]:
        return None, "invalid"
    values: dict = {"status": new_status, "updated_at": func.now()}
    if new_status == "acknowledged":
        values["acknowledged_at"] = func.now()
    changed = (
        await session.execute(
            update(WaiterRequest)
            .where(WaiterRequest.id == request_id, WaiterRequest.status == current)
            .values(**values)
            .returning(WaiterRequest.id)
        )
    ).scalar_one_or_none()
    if changed is None:  # lost a race with another PATCH
        return None, "invalid"
    wr = await load_waiter_request(session, WaiterRequest.id == request_id)
    return wr, None
