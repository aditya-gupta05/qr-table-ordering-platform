"""Turn and session outcome assignment (plan section 6). Assignment logic only; the math for
deflection lives in analytics_core."""

import uuid
from datetime import UTC, datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.chat import analytics_core as core

# strongest first
PRECEDENCE = (
    "error",
    "blocked",
    "escalation_offered",
    "off_topic",
    "not_found",
    "clarify",
    "action_only",
    "answered",
)


def assign_turn_outcome(
    *,
    error: bool,
    blocked: bool,
    status: str,
    actions: list[dict],
    grounded: bool,
    valid_citation: bool,
) -> str:
    """Server-assigned outcome. Precedence: error > blocked > escalation_offered > off_topic >
    not_found > clarify > action_only > answered. `answered` needs status answered, a grounding
    tool result (or a citation to something shown earlier) and a valid citation; an `answered`
    reply that only proposed cart items is `action_only`; anything else falls back to `clarify`
    because the diner did not get a grounded answer."""
    if error:
        return "error"
    if blocked:
        return "blocked"
    if status == "needs_staff" or any(a.get("type") == "call_waiter" for a in actions):
        return "escalation_offered"
    if status == "off_topic":
        return "off_topic"
    if status == "not_found":
        return "not_found"
    if status == "clarify":
        return "clarify"
    if status == "answered" and grounded and valid_citation:
        return "answered"
    if actions:
        return "action_only"
    return "clarify"


async def refresh_session_outcome(
    db: AsyncSession, session_id: uuid.UUID, now: datetime | None = None
) -> str | None:
    """Lazily label one session (idle >= 15 min) and persist it. Returns the stored/new outcome,
    or None while the session is still active. Does not commit."""
    now = now or datetime.now(UTC)
    row = (
        await db.execute(
            text("SELECT last_activity_at, outcome FROM chat_sessions WHERE id = :s"),
            {"s": session_id},
        )
    ).first()
    if row is None:
        return None
    if row.outcome:
        return row.outcome
    turns = [
        core.TurnRow(str(session_id), r.turn_outcome, r.created_at, r.feedback)
        for r in await db.execute(
            text(
                "SELECT turn_outcome, created_at, feedback FROM chat_messages "
                "WHERE session_id = :s AND role = 'assistant' AND turn_outcome IS NOT NULL"
            ),
            {"s": session_id},
        )
    ]
    waiters = [
        core.WaiterRow(str(session_id), r.created_at)
        for r in await db.execute(
            text("SELECT created_at FROM waiter_requests WHERE session_id = :s"), {"s": session_id}
        )
    ]
    outcome = core.session_outcome(turns, waiters, row.last_activity_at, now)
    if outcome:
        await db.execute(
            text(
                "UPDATE chat_sessions SET outcome = :o, outcome_at = :n "
                "WHERE id = :s AND outcome IS NULL"
            ),
            {"o": outcome, "n": now, "s": session_id},
        )
    return outcome


async def settle_idle_sessions(
    db: AsyncSession, now: datetime | None = None, limit: int = 200
) -> int:
    """Label up to `limit` idle, unlabelled sessions. Cheap enough to run when a session is
    created. Does not commit. Returns how many sessions got a label."""
    now = now or datetime.now(UTC)
    ids = (
        (
            await db.execute(
                text(
                    "SELECT id FROM chat_sessions WHERE outcome IS NULL "
                    "AND last_activity_at < :cut ORDER BY last_activity_at LIMIT :n"
                ),
                {"cut": now - core.SESSION_IDLE, "n": limit},
            )
        )
        .scalars()
        .all()
    )
    done = 0
    for sid in ids:
        if await refresh_session_outcome(db, sid, now):
            done += 1
    return done
