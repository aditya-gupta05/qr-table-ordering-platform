"""Turn outcome precedence and lazy session labels."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text

from app.chat.outcomes import assign_turn_outcome, refresh_session_outcome, settle_idle_sessions
from app.db import SessionLocal
from tests.chat_helpers import clean_chat_tables, rows

CALL = {"type": "call_waiter"}
CART = {"type": "add_to_cart"}


def out(**kw):
    base = dict(error=False, blocked=False, status="answered", actions=[], grounded=True,
                valid_citation=True)  # fmt: skip
    return assign_turn_outcome(**(base | kw))


def test_precedence_order():
    assert out(error=True, blocked=True, status="needs_staff", actions=[CALL]) == "error"
    assert out(blocked=True, actions=[CALL]) == "blocked"
    assert out(status="off_topic", actions=[CALL]) == "escalation_offered"
    assert out(status="needs_staff") == "escalation_offered"
    assert out(status="off_topic") == "off_topic"
    assert out(status="not_found", actions=[CART]) == "not_found"
    assert out(status="clarify", actions=[CART]) == "clarify"
    assert out(valid_citation=False, actions=[CART]) == "action_only"
    assert out(actions=[CART]) == "answered"  # info + citation + a cart proposal


def test_answered_requires_status_grounding_and_citation():
    assert out() == "answered"
    assert out(grounded=False) == "clarify"
    assert out(valid_citation=False) == "clarify"


@pytest.fixture(autouse=True)
async def clean():
    await clean_chat_tables()
    yield
    await clean_chat_tables()


NOW = datetime(2026, 10, 11, 12, 0, tzinfo=UTC)


async def make_session(
    turns: list[tuple[str, bool | None]], idle_min: int, waiter=False
) -> uuid.UUID:
    sid = uuid.uuid4()
    last = NOW - timedelta(minutes=idle_min)
    async with SessionLocal() as db:
        tid = (await db.execute(text("SELECT id FROM dining_tables WHERE label='T1'"))).scalar_one()
        await db.execute(
            text(
                "INSERT INTO chat_sessions (id, table_id, created_at, last_activity_at) "
                "VALUES (:i, :t, :c, :c)"
            ),
            {"i": sid, "t": tid, "c": last},
        )
        for n, (outcome, fb) in enumerate(turns, 1):
            await db.execute(
                text(
                    "INSERT INTO chat_messages (session_id, turn_id, turn_index, role, content, "
                    "turn_outcome, feedback, created_at) VALUES (:s, gen_random_uuid(), :n, "
                    "'assistant', 'x', :o, :f, :c)"
                ),
                {"s": sid, "n": n, "o": outcome, "f": fb, "c": last - timedelta(minutes=10 - n)},
            )
        if waiter:
            await db.execute(
                text("INSERT INTO waiter_requests (table_id, session_id, source, reason) "
                     "VALUES (:t, :s, 'chat', 'other')"),
                {"t": tid, "s": sid},
            )  # fmt: skip
        await db.commit()
    return sid


async def label(sid) -> str | None:
    async with SessionLocal() as db:
        got = await refresh_session_outcome(db, sid, NOW)
        await db.commit()
    stored = (await rows("SELECT outcome FROM chat_sessions WHERE id=:i", i=sid))[0][0]
    assert stored == got  # persisted
    return got


async def test_session_labels():
    assert await label(await make_session([("answered", None)], idle_min=5)) is None  # active
    assert await label(await make_session([("answered", None)], idle_min=30)) == "resolved"
    assert await label(await make_session([("off_topic", None)], idle_min=30)) == "no_question"
    assert await label(await make_session([], idle_min=30)) == "no_question"
    assert await label(await make_session([("answered", None)], 30, waiter=True)) == "escalated"
    assert await label(await make_session([("not_found", None)], idle_min=30)) == "abandoned"
    assert (
        await label(await make_session([("answered", None), ("not_found", None)], 30))
        == "abandoned"
    )
    assert await label(await make_session([("answered", False)], idle_min=30)) == "abandoned"
    assert (
        await label(await make_session([("not_found", None), ("answered", None)], 30)) == "resolved"
    )


async def test_settle_idle_sessions_only_labels_idle_ones():
    idle = await make_session([("answered", None)], idle_min=60)
    active = await make_session([("answered", None)], idle_min=1)
    async with SessionLocal() as db:
        assert await settle_idle_sessions(db, NOW) == 1
        await db.commit()
    got = {r[0]: r[1] for r in await rows("SELECT id, outcome FROM chat_sessions")}
    assert got[idle] == "resolved" and got[active] is None
