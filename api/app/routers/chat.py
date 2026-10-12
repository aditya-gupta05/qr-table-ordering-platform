"""Chat endpoints (contract: docs/contracts/chat.md). Thin HTTP shell over app.chat.service."""

from typing import Annotated, Literal

import structlog
from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.chat import service
from app.chat.orchestrator import ChatDeps
from app.db import get_session

router = APIRouter(prefix="/api/chat", tags=["chat"])


class SessionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    table_token: str


class MessageIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str
    client_message_id: Annotated[str, Field(min_length=1, max_length=64)]

    @field_validator("text")
    @classmethod
    def _text(cls, v: str) -> str:
        v = v.strip()
        if not 1 <= len(v) <= service.MAX_MESSAGE_CHARS:
            raise ValueError(f"text must be 1 to {service.MAX_MESSAGE_CHARS} characters")
        return v


class DecisionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: Literal["accepted", "dismissed"]


class FeedbackIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    helpful: bool


@router.post("/sessions", status_code=201)
async def create_session(
    body: SessionIn,
    db: AsyncSession = Depends(get_session),
    deps: ChatDeps = Depends(service.get_chat_deps),
) -> dict:
    return await service.create_session(db, deps, body.table_token)


@router.get("/sessions/{sid}")
async def get_session_(
    sid: str,
    db: AsyncSession = Depends(get_session),
    deps: ChatDeps = Depends(service.get_chat_deps),
) -> dict:
    return await service.get_session(db, deps, service.parse_uuid(sid, "session"))


@router.post("/sessions/{sid}/messages")
async def post_message(
    sid: str,
    body: MessageIn,
    request: Request,
    db: AsyncSession = Depends(get_session),
    deps: ChatDeps = Depends(service.get_chat_deps),
):
    begun = await service.begin_turn(
        db, deps, service.parse_uuid(sid, "session"), body.text, body.client_message_id
    )
    request_id = structlog.contextvars.get_contextvars().get("request_id")
    if "text/event-stream" in request.headers.get("accept", ""):
        await db.close()  # release the pooled connection before a potentially long stream
        return StreamingResponse(
            service.stream_turn(deps, begun, request_id),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )
    await db.close()
    return await service.run_json(deps, begun, request_id, deps.settings.debug)


@router.post("/sessions/{sid}/actions/{action_id}")
async def decide_action(
    sid: str,
    action_id: str,
    body: DecisionIn,
    db: AsyncSession = Depends(get_session),
) -> dict:
    return await service.decide_action(
        db,
        service.parse_uuid(sid, "action"),
        service.parse_uuid(action_id, "action"),
        body.decision,
    )


@router.post("/messages/{message_id}/feedback", status_code=204)
async def feedback(
    message_id: int, body: FeedbackIn, db: AsyncSession = Depends(get_session)
) -> Response:
    await service.set_feedback(db, message_id, body.helpful)
    return Response(status_code=204)
