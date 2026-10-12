import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import waiter as svc
from app.db import get_session
from app.models import DiningTable, WaiterRequest
from app.waiter import WaiterPatchIn, WaiterRequestIn, WaiterRequestOut

# NOTE: no auth (same documented known gap as the kitchen endpoints).
router = APIRouter(prefix="/api", tags=["waiter"])
log = structlog.get_logger("waiter")


@router.post("/waiter-requests", response_model=WaiterRequestOut, status_code=201)
async def create_request(
    body: WaiterRequestIn, response: Response, session: AsyncSession = Depends(get_session)
):
    table_id = (
        await session.execute(
            select(DiningTable.id).where(DiningTable.qr_token == body.table_token)
        )
    ).scalar_one_or_none()
    if table_id is None:
        raise HTTPException(404, "Unknown table")
    wr, created = await svc.create_waiter_request(
        session, table_id, body.reason, body.note, "button", body.session_id
    )
    await session.commit()
    if not created:
        response.status_code = 200
    await svc.announce(wr, created)  # strictly after commit
    return wr


@router.get("/waiter-requests")
async def list_requests(
    status: svc.Status = Query("open"), session: AsyncSession = Depends(get_session)
) -> dict:
    rows = (
        await session.execute(
            select(WaiterRequest, DiningTable.label)
            .join(DiningTable, DiningTable.id == WaiterRequest.table_id)
            .where(WaiterRequest.status == status)
            .order_by(WaiterRequest.created_at, WaiterRequest.id)
        )
    ).all()
    return {"items": [svc.to_out(wr, label).model_dump(mode="json") for wr, label in rows]}


@router.patch("/waiter-requests/{request_id}", response_model=WaiterRequestOut)
async def patch_request(
    request_id: int, body: WaiterPatchIn, session: AsyncSession = Depends(get_session)
):
    wr, err = await svc.change_status(session, request_id, body.status)
    if err == "unknown":
        raise HTTPException(404, "Unknown waiter request")
    if err == "invalid":
        await session.rollback()
        raise HTTPException(409, "Invalid transition")
    await session.commit()
    assert wr is not None
    log.info("waiter_request_updated", waiter_request_id=wr.id, status=wr.status)
    await svc.publish(wr, "waiter.updated")
    return wr
