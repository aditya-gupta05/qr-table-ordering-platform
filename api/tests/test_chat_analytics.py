"""Seeds chat rows in a fixed 2020 window (isolated from rows other tests create) and checks the
exact numbers GET /api/analytics/chat returns."""

import json
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.chat import analytics_core as core

WIN = {"since": "2020-06-01T00:00:00Z", "until": "2020-06-02T00:00:00Z"}
EMPTY = {"since": "1999-01-01T00:00:00Z", "until": "1999-01-02T00:00:00Z"}


def ts(hms: str) -> datetime:
    parts = [int(x) for x in hms.split(":")] + [0]
    return datetime(2020, 6, 1, parts[0], parts[1], parts[2], tzinfo=UTC)


@pytest.fixture
async def db(seeded_test_db):
    engine = create_async_engine(seeded_test_db)
    yield engine
    await engine.dispose()


async def wipe(conn):
    await conn.execute(text("DELETE FROM waiter_requests WHERE created_at < '2021-01-01'"))
    await conn.execute(text("DELETE FROM chat_sessions WHERE created_at < '2021-01-01'"))


@pytest.fixture
async def seeded(db):
    async with db.begin() as conn:
        await wipe(conn)
        table_id = (
            await conn.execute(text("SELECT id FROM dining_tables WHERE label='T1'"))
        ).scalar_one()

        async def session(sid):
            await conn.execute(
                text(
                    "INSERT INTO chat_sessions (id, table_id, created_at, last_activity_at) "
                    "VALUES (:i, :t, :c, :c)"
                ),
                {"i": sid, "t": table_id, "c": ts("10:00")},
            )

        async def msg(sid, idx, outcome, at, feedback=None, latency=None, guard=()):
            tid = uuid.uuid4()
            await conn.execute(
                text(
                    "INSERT INTO chat_messages (session_id, turn_id, turn_index, role, content, "
                    "client_message_id) VALUES (:s, :t, :i, 'user', 'q', :cm)"
                ),
                {"s": sid, "t": tid, "i": idx, "cm": str(tid)},
            )
            await conn.execute(
                text(
                    "INSERT INTO chat_messages (session_id, turn_id, turn_index, role, content, "
                    "turn_outcome, feedback, latency_ms, guardrails, created_at) VALUES "
                    "(:s, :t, :i, 'assistant', 'a', :o, :f, :l, CAST(:g AS jsonb), :c)"
                ),
                {
                    "s": sid,
                    "t": tid,
                    "i": idx,
                    "o": outcome,
                    "f": feedback,
                    "l": latency,
                    "g": json.dumps(list(guard)),
                    "c": ts(at),
                },
            )
            return tid

        async def llm(sid, tid, idx, hit, miss, comp, cost, lat, status="ok"):
            await conn.execute(
                text(
                    "INSERT INTO llm_calls (session_id, turn_id, call_index, model, "
                    "cache_hit_tokens, cache_miss_tokens, completion_tokens, cost_usd_micros, "
                    "latency_ms, status, created_at) VALUES (:s,:t,:i,'deepseek-flash',:h,:m,:c,"
                    ":cost,:l,:st,:at)"
                ),
                {
                    "s": sid,
                    "t": tid,
                    "i": idx,
                    "h": hit,
                    "m": miss,
                    "c": comp,
                    "cost": cost,
                    "l": lat,
                    "st": status,
                    "at": ts("10:00"),
                },
            )

        async def tool(sid, tid, name, status, lat):
            await conn.execute(
                text(
                    "INSERT INTO tool_calls (session_id, turn_id, name, status, latency_ms, "
                    "created_at) VALUES (:s,:t,:n,:st,:l,:at)"
                ),
                {"s": sid, "t": tid, "n": name, "st": status, "l": lat, "at": ts("10:00")},
            )

        async def action(sid, tid, kind, status):
            await conn.execute(
                text(
                    "INSERT INTO chat_actions (session_id, turn_id, kind, status, created_at) "
                    "VALUES (:s,:t,:k,:st,:at)"
                ),
                {"s": sid, "t": tid, "k": kind, "st": status, "at": ts("10:00")},
            )

        s = [uuid.uuid4() for _ in range(5)]
        for sid in s:
            await session(sid)
        t1 = await msg(s[0], 1, "answered", "10:00:00", latency=1500)
        await llm(s[0], t1, 1, 800, 200, 50, 1000, 400)
        await llm(s[0], t1, 2, 900, 100, 70, 1500, 600)
        await tool(s[0], t1, "search_menu", "ok", 50)
        await tool(s[0], t1, "search_info", "ok", 30)
        await action(s[0], t1, "add_to_cart", "accepted")
        await action(s[0], t1, "add_to_cart", "dismissed")

        t2 = await msg(s[1], 1, "answered", "10:00:00", latency=2500, guard=["price_mismatch"])
        await llm(s[1], t2, 1, 0, 0, 0, 0, 1000, status="error")
        await tool(s[1], t2, "search_menu", "ok", 20)
        await tool(s[1], t2, "search_menu", "error", 20)
        await action(s[1], t2, "call_waiter", "accepted")
        await action(s[1], t2, "call_waiter", "dismissed")
        await action(s[1], t2, "add_to_cart", "proposed")
        await conn.execute(
            text(
                "INSERT INTO waiter_requests (table_id, session_id, source, reason, created_at) "
                "VALUES (:t, :s, 'chat', 'other', :c)"
            ),
            {"t": table_id, "s": s[1], "c": ts("10:05")},
        )
        await msg(
            s[2],
            1,
            "answered",
            "10:00:00",
            feedback=False,
            guard=[{"check": "allergen_claim"}, "price_mismatch"],
        )
        await msg(s[3], 1, "not_found", "10:00:00")
        await msg(s[3], 2, "off_topic", "10:01:00")
        await msg(s[4], 1, "off_topic", "10:00:00")
    yield
    async with db.begin() as conn:
        await wipe(conn)


async def test_exact_numbers(client, seeded):
    j = (await client.get("/api/analytics/chat", params=WIN)).json()

    assert j["sessions"]["total"] == 5
    assert j["sessions"]["outcomes"] == {
        "resolved": 1,
        "escalated": 1,
        "abandoned": 2,
        "no_question": 1,
        "active": 0,
    }
    assert j["turns"]["total"] == 6 and j["turns"]["in_scope"] == 4
    assert j["turns"]["outcomes"]["answered"] == 3
    assert j["turns"]["outcomes"]["not_found"] == 1
    assert j["turns"]["outcomes"]["off_topic"] == 2
    assert j["turns"]["outcomes"]["error"] == 0

    td, sd = j["deflection"]["turn"], j["deflection"]["session"]
    assert (td["numerator"], td["denominator"], td["rate"]) == (1, 4, 0.25)
    lo, hi = core.wilson_ci(1, 4)
    assert td["ci95"] == pytest.approx([lo, hi])
    assert (sd["numerator"], sd["denominator"], sd["rate"]) == (1, 4, 0.25)

    assert j["escalations"] == {"offered": 2, "confirmed": 1}
    assert j["cart"] == {"proposed": 3, "accepted": 1}

    llm = j["llm"]
    assert llm["calls"] == 3 and llm["errors"] == 1
    assert llm["tokens"] == {
        "cache_hit": 1700,
        "cache_miss": 300,
        "completion": 120,
        "reasoning": 0,
    }
    assert llm["cache_hit_ratio"] == pytest.approx(0.85)
    assert llm["cost_usd"]["total"] == pytest.approx(0.0025)
    assert llm["cost_usd"]["per_turn"] == pytest.approx(0.0025 / 6)
    assert llm["cost_usd"]["per_session"] == pytest.approx(0.0025 / 5)
    assert llm["calls_per_turn"] == pytest.approx(0.5)
    assert llm["call_latency_ms"] == {"p50": 600.0, "p95": pytest.approx(960.0)}
    assert j["turn_latency_ms"] == {"p50": 2000.0, "p95": pytest.approx(2450.0)}

    assert j["tools"]["by_tool"] == {
        "search_info": {"calls": 1, "errors": 0},
        "search_menu": {"calls": 3, "errors": 1},
    }
    assert (j["tools"]["calls"], j["tools"]["errors"]) == (4, 1)
    assert j["guardrails"] == {"total": 3, "by_check": {"price_mismatch": 2, "allergen_claim": 1}}

    sp = j["latency_split"]
    assert (sp["turns"], sp["total_ms"], sp["llm_ms"], sp["retrieval_ms"], sp["other_ms"]) == (
        2,
        4000,
        2000,
        120,
        1880,
    )
    assert sp["share"]["llm"] == pytest.approx(0.5)


async def test_window_excludes_rows(client, seeded):
    j = (await client.get("/api/analytics/chat", params=EMPTY)).json()
    assert j["sessions"]["total"] == 0 and j["turns"]["total"] == 0


async def test_empty_window_is_zeros_not_errors(client):
    r = await client.get("/api/analytics/chat", params=EMPTY)
    assert r.status_code == 200
    j = r.json()
    assert j["sessions"]["total"] == 0 and j["turns"]["total"] == 0
    assert j["deflection"]["turn"] == {"numerator": 0, "denominator": 0, "rate": None, "ci95": None}
    assert j["llm"]["calls"] == 0 and j["llm"]["cache_hit_ratio"] is None
    assert j["llm"]["cost_usd"] == {"total": 0.0, "per_turn": None, "per_session": None}
    assert j["turn_latency_ms"] == {"p50": None, "p95": None}
    assert j["tools"] == {"calls": 0, "errors": 0, "by_tool": {}}
    assert j["guardrails"] == {"total": 0, "by_check": {}}
    assert j["escalations"] == {"offered": 0, "confirmed": 0}
    assert j["latency_split"]["share"] == {"llm": None, "retrieval": None, "other": None}
    assert all(v == 0 for v in j["turns"]["outcomes"].values())


async def test_no_params_and_bad_params(client):
    assert (await client.get("/api/analytics/chat")).status_code == 200
    assert (await client.get("/api/analytics/chat", params={"since": "garbage"})).status_code == 422
