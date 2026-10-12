"""Chat service layer: sessions, turn admission (limits, busy lock, replay), turn execution and
persistence, actions, feedback, Message serialisation. The router in app/routers/chat.py is a thin
HTTP shell over this module; the contract is docs/contracts/chat.md.

Turn lifecycle (two short transactions around the LLM loop; no connection is held during it):

    begin_turn      FOR UPDATE on the session row -> replay? busy? limits? -> insert the user
                    message, claim busy_until, count the turn -> commit
    run_turn        (orchestrator) LLM loop with its own short sessions
    persist_result  insert assistant message + chat_actions, release busy_until -> commit

If the client disconnects, the turn task is cancelled and its `finally` releases busy_until. The
user message stays; a retry with the same client_message_id re-runs the turn without consuming a
second turn from the session limit.
"""

import asyncio
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from fastapi import HTTPException
from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app import waiter as waiter_svc
from app.chat import answer_cache, metrics, outcomes
from app.chat.allergen_terms import extract_declaration
from app.chat.orchestrator import ERROR_TEXT, ChatDeps, TurnInput, TurnResult, run_turn, sha12
from app.chat.redact import redact
from app.db import SessionLocal
from app.models import (
    ChatAction,
    ChatMessage,
    ChatSession,
    DiningTable,
    WaiterRequest,
)

log = structlog.get_logger("chat")

MAX_MESSAGE_CHARS = 500
BUSY_GRACE_S = 15.0
PING_INTERVAL_S = 10.0

LIMIT_MESSAGES = {
    "session_limit": "You have reached the question limit for this chat. "
    "Please ask a member of staff or tap Call a waiter.",
    "table_rate": "This table has asked a lot of questions recently. "
    "Please try again in a little while, or tap Call a waiter.",
    "budget": "The assistant is unavailable right now. Please ask a member of staff "
    "or tap Call a waiter.",
}

# --- dependency injection -----------------------------------------------------------------------

_deps: ChatDeps | None = None


def get_chat_deps() -> ChatDeps:
    """FastAPI dependency. Tests override it with a ChatDeps built around a FakeLLM."""
    global _deps
    if _deps is None:
        from app.chat.llm import DeepSeekClient
        from app.chat.settings import get_chat_settings

        settings = get_chat_settings()
        _deps = ChatDeps(llm=DeepSeekClient(settings), settings=settings, sessionmaker=SessionLocal)
    return _deps


async def close_chat_deps() -> None:
    global _deps
    if _deps is not None:
        await _deps.llm.aclose()
        _deps = None


# --- serialisation -----------------------------------------------------------------------------


def iso(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def action_out(row: ChatAction) -> dict[str, Any]:
    return {**row.payload, "status": row.status}


def message_out(row: ChatMessage, actions: list[ChatAction] | None = None) -> dict[str, Any]:
    user = row.role == "user"
    return {
        "id": row.id,
        "role": row.role,
        "text": row.content,
        "status": None if user else row.status,
        "citations": [] if user else (row.citations or []),
        "allergen_notice": None if user else row.allergen_notice,
        "actions": [] if user else [action_out(a) for a in (actions or [])],
        "feedback": None if user else row.feedback,
        "created_at": iso(row.created_at),
    }


# --- sessions ------------------------------------------------------------------------------------


def parse_uuid(value: str, what: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError:
        raise HTTPException(404, f"Unknown {what}") from None


def session_config(deps: ChatDeps) -> dict[str, Any]:
    s = deps.settings
    cfg = {
        "model": s.model,
        "thinking": s.thinking,
        "temperature": s.temperature,
        "max_tokens": s.max_tokens,
        "max_llm_calls": s.max_llm_calls,
        "strict_tools": s.strict_tools,
        "menu_context": s.menu_context,
        "abstain_signal": s.abstain_signal,
        "abstain_threshold": s.abstain_threshold,
        "history_turns": s.history_turns,
        "answer_cache": s.answer_cache,
    }
    cfg["config_hash"] = sha12(json.dumps(cfg, sort_keys=True))
    return cfg


async def create_session(db: AsyncSession, deps: ChatDeps, table_token: str) -> dict[str, Any]:
    table = (
        await db.execute(select(DiningTable).where(DiningTable.qr_token == table_token))
    ).scalar_one_or_none()
    if table is None:
        raise HTTPException(404, "Unknown table")
    await outcomes.settle_idle_sessions(db)
    sess = ChatSession(table_id=table.id, config=session_config(deps))
    db.add(sess)
    await db.commit()
    log.info("chat_session_created", session_id=str(sess.id), table=table.label)
    return {
        "session_id": str(sess.id),
        "table_label": table.label,
        "max_message_chars": MAX_MESSAGE_CHARS,
        "turns_remaining": deps.settings.session_turn_limit,
    }


async def _actions_by_turn(db: AsyncSession, session_id: uuid.UUID) -> dict[uuid.UUID, list]:
    rows = (
        (
            await db.execute(
                select(ChatAction)
                .where(ChatAction.session_id == session_id)
                .order_by(ChatAction.created_at, ChatAction.id)
            )
        )
        .scalars()
        .all()
    )
    out: dict[uuid.UUID, list[ChatAction]] = {}
    for a in rows:
        out.setdefault(a.turn_id, []).append(a)
    return out


async def get_session(db: AsyncSession, deps: ChatDeps, sid: uuid.UUID) -> dict[str, Any]:
    row = (
        await db.execute(
            select(ChatSession, DiningTable.label)
            .join(DiningTable, DiningTable.id == ChatSession.table_id)
            .where(ChatSession.id == sid)
        )
    ).first()
    if row is None:
        raise HTTPException(404, "Unknown session")
    sess, label = row
    msgs = (
        (
            await db.execute(
                select(ChatMessage)
                .where(ChatMessage.session_id == sid)
                .order_by(ChatMessage.turn_index, ChatMessage.role.desc(), ChatMessage.id)
            )
        )
        .scalars()
        .all()
    )
    acts = await _actions_by_turn(db, sid)
    await outcomes.refresh_session_outcome(db, sid)
    await db.commit()
    return {
        "session_id": str(sid),
        "table_label": label,
        "turns_remaining": max(deps.settings.session_turn_limit - sess.turn_count, 0),
        "messages": [message_out(m, acts.get(m.turn_id)) for m in msgs],
    }


# --- turn admission -----------------------------------------------------------------------------


@dataclass
class BegunTurn:
    session_id: uuid.UUID
    table_id: int
    table_label: str
    turn_id: uuid.UUID
    turn_index: int
    text: str
    declared: tuple[str, ...]
    replay: dict[str, Any] | None = None  # {"turn_id", "outcome", "message", "latency_ms"}


def _limit(reason: str) -> HTTPException:
    return HTTPException(429, {"message": LIMIT_MESSAGES[reason], "reason": reason})


async def begin_turn(
    db: AsyncSession, deps: ChatDeps, sid: uuid.UUID, raw_text: str, client_message_id: str
) -> BegunTurn:
    """Admit a turn or raise 404 / 409 / 429. Replays a stored reply for a repeated
    client_message_id. Commits."""
    s = deps.settings
    now = datetime.now(UTC)
    row = (
        await db.execute(
            select(ChatSession, DiningTable.label)
            .join(DiningTable, DiningTable.id == ChatSession.table_id)
            .where(ChatSession.id == sid)
            .with_for_update(of=ChatSession)
        )
    ).first()
    if row is None:
        raise HTTPException(404, "Unknown session")
    sess, label = row

    existing = (
        await db.execute(
            select(ChatMessage).where(
                ChatMessage.session_id == sid, ChatMessage.client_message_id == client_message_id
            )
        )
    ).scalar_one_or_none()

    text_redacted = redact(raw_text)
    rerun = False
    if existing is not None:
        reply = (
            await db.execute(
                select(ChatMessage).where(
                    ChatMessage.session_id == sid,
                    ChatMessage.turn_index == existing.turn_index,
                    ChatMessage.role == "assistant",
                )
            )
        ).scalar_one_or_none()
        if reply is not None and reply.turn_outcome != "error":
            acts = (
                (await db.execute(select(ChatAction).where(ChatAction.turn_id == reply.turn_id)))
                .scalars()
                .all()
            )
            begun = BegunTurn(
                sid, sess.table_id, label, reply.turn_id, reply.turn_index, existing.content,
                tuple(sess.declared_allergens or ()),
                replay={
                    "turn_id": str(reply.turn_id),
                    "outcome": reply.turn_outcome,
                    "message": message_out(reply, list(acts)),  # before rollback expires rows
                    "latency_ms": reply.latency_ms or 0,
                },
            )  # fmt: skip
            await db.rollback()
            return begun
        rerun = True  # crashed / cancelled / failed earlier: run it again, same slot

    if sess.busy_until is not None and sess.busy_until > now:
        await db.rollback()
        raise HTTPException(409, "Turn in progress")

    if not rerun:
        if sess.turn_count >= s.session_turn_limit:
            await db.rollback()
            raise _limit("session_limit")
        recent = (
            await db.execute(
                text(
                    "SELECT count(*) FROM chat_messages m JOIN chat_sessions cs "
                    "ON cs.id = m.session_id WHERE cs.table_id = :t AND m.role = 'user' "
                    "AND m.created_at > now() - interval '1 hour'"
                ),
                {"t": sess.table_id},
            )
        ).scalar_one()
        if recent >= s.table_turns_per_hour:
            await db.rollback()
            raise _limit("table_rate")
    spent = (
        await db.execute(
            text(
                "SELECT coalesce(sum(cost_usd_micros), 0) FROM llm_calls WHERE created_at >= "
                "date_trunc('day', now() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC'"
            )
        )
    ).scalar_one()
    if spent >= s.daily_budget_usd * 1_000_000:
        await db.rollback()
        raise _limit("budget")

    # Declared allergies only ever grow (never removed automatically).
    declared = tuple(
        sorted(
            set(sess.declared_allergens or ()) | set(extract_declaration(text_redacted).allergens)
        )
    )
    turn_id = uuid.uuid4()
    if rerun:
        assert existing is not None
        turn_index = existing.turn_index
        text_redacted = existing.content
        await db.execute(
            text(
                "DELETE FROM chat_actions WHERE turn_id IN "
                "(SELECT turn_id FROM chat_messages WHERE session_id = :s AND turn_index = :i "
                " AND role = 'assistant')"
            ),
            {"s": sid, "i": turn_index},
        )
        await db.execute(
            text(
                "DELETE FROM chat_messages WHERE session_id = :s AND turn_index = :i "
                "AND role = 'assistant'"
            ),
            {"s": sid, "i": turn_index},
        )
        existing.turn_id = turn_id
    else:
        turn_index = sess.turn_count + 1
        sess.turn_count = turn_index
        db.add(
            ChatMessage(
                session_id=sid,
                turn_id=turn_id,
                turn_index=turn_index,
                role="user",
                content=text_redacted,
                content_llm=text_redacted,
                client_message_id=client_message_id,
                request_id=_request_id(),
            )
        )
    sess.declared_allergens = list(declared)
    sess.busy_until = now + timedelta(seconds=s.turn_deadline_s + BUSY_GRACE_S)
    sess.last_activity_at = now
    sess.outcome = None  # activity after a label reopens the session
    sess.outcome_at = None
    await db.commit()
    return BegunTurn(sid, sess.table_id, label, turn_id, turn_index, text_redacted, declared)


def _request_id() -> str | None:
    return structlog.contextvars.get_contextvars().get("request_id")


# --- turn execution ----------------------------------------------------------------------------


async def release_busy(deps: ChatDeps, sid: uuid.UUID) -> None:
    async with deps.sessionmaker() as db:
        await db.execute(update(ChatSession).where(ChatSession.id == sid).values(busy_until=None))
        await db.commit()


async def persist_result(deps: ChatDeps, begun: BegunTurn, result: TurnResult) -> dict[str, Any]:
    """Store the assistant message + actions, release the busy lock. Returns the Message."""
    async with deps.sessionmaker() as db:
        msg = ChatMessage(
            session_id=begun.session_id,
            turn_id=begun.turn_id,
            turn_index=begun.turn_index,
            role="assistant",
            content=result.text,
            status=result.status,
            turn_outcome=result.outcome,
            citations=result.citations,
            allergen_notice=result.allergen_notice,
            guardrails=result.guardrails,
            latency_ms=result.latency_ms,
            request_id=_request_id(),
        )
        db.add(msg)
        await db.flush()
        rows = []
        for a in result.actions:
            payload = {k: v for k, v in a.items() if k != "status"}
            row = ChatAction(
                id=uuid.UUID(a["action_id"]),
                session_id=begun.session_id,
                turn_id=begun.turn_id,
                kind=a["type"],
                payload=payload,
                status="proposed",
            )
            db.add(row)
            rows.append(row)
        await db.flush()
        if deps.settings.answer_cache == "shadow" and result.cache_candidate is not None:
            await answer_cache.record_shadow(db, begun.turn_id, result.cache_candidate, msg.id)
        await db.execute(
            update(ChatSession)
            .where(ChatSession.id == begun.session_id)
            .values(
                busy_until=None,
                last_activity_at=func.now(),
                prompt_version=result.prompt_version or None,
            )
        )
        await db.commit()
        await db.refresh(msg)
        return message_out(msg, rows)


async def execute_turn(
    deps: ChatDeps, begun: BegunTurn, request_id: str | None, emit
) -> tuple[TurnResult, dict[str, Any]]:
    """Run + persist one admitted turn. busy_until is released on every path, including
    cancellation (client disconnect) and unexpected errors."""
    if request_id:
        structlog.contextvars.bind_contextvars(request_id=request_id)
    inp = TurnInput(
        session_id=begun.session_id,
        turn_id=begun.turn_id,
        turn_index=begun.turn_index,
        table_label=begun.table_label,
        declared=begun.declared,
        text=begun.text,
        request_id=request_id,
    )
    try:
        result = await run_turn(deps, inp, emit)
        message = await persist_result(deps, begun, result)
    except asyncio.CancelledError:
        log.info("chat_turn_cancelled", turn_id=str(begun.turn_id))
        await release_busy(deps, begun.session_id)
        raise
    except Exception:
        log.exception("chat_turn_persist_failed", turn_id=str(begun.turn_id))
        await release_busy(deps, begun.session_id)
        raise
    return result, message


def synthetic_fallback() -> dict[str, Any]:
    """Only used if even persisting the real fallback failed (DB down)."""
    return {
        "id": 0,
        "role": "assistant",
        "text": ERROR_TEXT,
        "status": "error",
        "citations": [],
        "allergen_notice": None,
        "actions": [],
        "feedback": None,
        "created_at": iso(datetime.now(UTC)),
    }


def done_frame(begun: BegunTurn, result: TurnResult) -> dict[str, Any]:
    return {
        "turn_id": str(begun.turn_id),
        "outcome": result.outcome,
        "latency_ms": result.latency_ms,
    }


def sse(event: str, data: dict[str, Any]) -> bytes:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n".encode()


ERROR_MESSAGES = {
    "llm_unavailable": "Sorry, I can't answer right now.",
    "timeout": "Sorry, that took too long.",
    "budget": "The assistant is unavailable right now.",
    "internal": "Sorry, something went wrong.",
}


async def stream_turn(deps: ChatDeps, begun: BegunTurn, request_id: str | None):
    """SSE byte stream for one admitted (or replayed) turn: turn, status*, answer|error, done.

    The turn runs in its own task so a slow client cannot stall it and a disconnect can cancel it:
    when this generator is closed or cancelled its `finally` cancels the task, and the task's own
    handler releases busy_until."""
    if request_id:
        structlog.contextvars.bind_contextvars(request_id=request_id)
    rid = request_id or ""
    yield sse(
        "turn", {"turn_id": str(begun.turn_id), "turn_index": begun.turn_index, "request_id": rid}
    )
    if begun.replay is not None:
        yield sse("answer", {"message": begun.replay["message"]})
        yield sse(
            "done",
            {
                "turn_id": begun.replay["turn_id"],
                "outcome": begun.replay["outcome"],
                "latency_ms": begun.replay["latency_ms"],
            },
        )
        return

    queue: asyncio.Queue = asyncio.Queue()

    async def emit(event: str, data: dict) -> None:
        await queue.put((event, data))

    async def job() -> None:
        try:
            result, message = await execute_turn(deps, begun, request_id, emit)
            if result.is_error:
                await queue.put(
                    (
                        "error",
                        {
                            "code": result.error_code or "internal",
                            "message": ERROR_MESSAGES.get(result.error_code or "", ""),
                            "fallback": message,
                        },
                    )
                )
            else:
                await queue.put(("answer", {"message": message}))
            await queue.put(("done", done_frame(begun, result)))
        except asyncio.CancelledError:
            raise
        except Exception:
            await queue.put(
                (
                    "error",
                    {
                        "code": "internal",
                        "message": ERROR_MESSAGES["internal"],
                        "fallback": synthetic_fallback(),
                    },
                )
            )
            await queue.put(
                ("done", {"turn_id": str(begun.turn_id), "outcome": "error", "latency_ms": 0})
            )
        finally:
            await queue.put(None)

    task = asyncio.create_task(job(), name=f"chat-turn-{begun.turn_id}")
    try:
        while True:
            try:
                item = await asyncio.wait_for(queue.get(), timeout=PING_INTERVAL_S)
            except TimeoutError:
                yield b": ping\n\n"
                continue
            if item is None:
                break
            yield sse(*item)
    finally:
        if not task.done():
            task.cancel()  # job's CancelledError handler releases busy_until


async def run_json(
    deps: ChatDeps, begun: BegunTurn, request_id: str | None, debug: bool
) -> dict[str, Any]:
    if begun.replay is not None:
        return {
            "turn_id": begun.replay["turn_id"],
            "outcome": begun.replay["outcome"],
            "message": begun.replay["message"],
        }

    async def emit(event: str, data: dict) -> None:
        return None

    result, message = await execute_turn(deps, begun, request_id, emit)
    body: dict[str, Any] = {
        "turn_id": str(begun.turn_id),
        "outcome": result.outcome,
        "message": message,
    }
    if debug:
        body["debug"] = result.debug
    return body


# --- actions and feedback ----------------------------------------------------------------------


async def decide_action(
    db: AsyncSession, sid: uuid.UUID, action_id: uuid.UUID, decision: str
) -> dict[str, Any]:
    row = (
        await db.execute(
            select(ChatAction)
            .where(ChatAction.id == action_id, ChatAction.session_id == sid)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(404, "Unknown action")
    table_id = (
        await db.execute(select(ChatSession.table_id).where(ChatSession.id == sid))
    ).scalar_one()

    async def existing_waiter():
        if row.kind != "call_waiter" or row.status != "accepted":
            return None
        wr = await waiter_svc.load_waiter_request(db, WaiterRequest.action_id == action_id)
        return wr or await waiter_svc.load_waiter_request(
            db, WaiterRequest.table_id == table_id, WaiterRequest.status == "open"
        )

    if row.status != "proposed":
        if row.status != decision:
            await db.rollback()
            raise HTTPException(409, "Action already resolved")
        wr = await existing_waiter()
        out = {
            "action": action_out(row),
            "waiter_request": wr.model_dump(mode="json") if wr else None,
        }
        await db.rollback()
        return out

    wr = None
    created = False
    if decision == "accepted":
        if row.kind == "call_waiter":
            wr, created = await waiter_svc.create_waiter_request(
                db,
                table_id,
                row.payload.get("reason", "other"),
                row.payload.get("note"),
                "chat",
                session_id=sid,
                action_id=action_id,
            )
        else:
            await db.execute(
                update(ChatSession).where(ChatSession.id == sid).values(converted=True)
            )
    row.status = decision
    row.resolved_at = datetime.now(UTC)
    await db.commit()
    if wr is not None:
        metrics.CHAT_ESCALATIONS.labels("confirmed").inc()
        await waiter_svc.announce(wr, created)  # strictly after the commit
    return {"action": action_out(row), "waiter_request": wr.model_dump(mode="json") if wr else None}


async def set_feedback(db: AsyncSession, message_id: int, helpful: bool) -> None:
    role = (
        await db.execute(select(ChatMessage.role).where(ChatMessage.id == message_id))
    ).scalar_one_or_none()
    if role is None:
        raise HTTPException(404, "Unknown message")
    if role != "assistant":
        raise HTTPException(422, "Feedback is only accepted for assistant messages")
    await db.execute(
        update(ChatMessage).where(ChatMessage.id == message_id).values(feedback=helpful)
    )
    await db.commit()
