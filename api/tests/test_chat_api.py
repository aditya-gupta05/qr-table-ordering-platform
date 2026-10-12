"""HTTP-level chat tests: SSE framing, JSON mode, error statuses, replay, actions, feedback."""

import asyncio
import json
import logging

import pytest
import redis.asyncio as aioredis

import app.main  # noqa: F401  (configure logging before caplog is installed)
from app import events
from app.chat import service
from app.main import app as fastapi_app
from tests.chat_helpers import (
    FakeLLM,
    clean_chat_tables,
    make_deps,
    reply,
    respond,
    rows,
    tc,
)


@pytest.fixture(autouse=True)
async def clean():
    await clean_chat_tables()
    yield
    fastapi_app.dependency_overrides.clear()
    await clean_chat_tables()


def use(llm: FakeLLM, **settings):
    deps = make_deps(llm, **settings)
    fastapi_app.dependency_overrides[service.get_chat_deps] = lambda: deps
    return deps


async def token(label="T1") -> str:
    return (await rows("SELECT qr_token FROM dining_tables WHERE label=:l", l=label))[0][0]


async def start(client, label="T1") -> str:
    r = await client.post("/api/chat/sessions", json={"table_token": await token(label)})
    assert r.status_code == 201, r.text
    return r.json()["session_id"]


def msg(text="hello", cmid="c-1"):
    return {"text": text, "client_message_id": cmid}


SSE = {"Accept": "text/event-stream"}
JSON = {"Accept": "application/json"}


def frames(body: str) -> list[tuple[str, dict]]:
    out = []
    for block in body.split("\n\n"):
        lines = [ln for ln in block.splitlines() if not ln.startswith(":")]
        if not lines:
            continue
        ev = lines[0].removeprefix("event: ")
        out.append((ev, json.loads(lines[1].removeprefix("data: "))))
    return out


OFF_TOPIC = lambda: reply(respond("Only restaurant questions, sorry.", "off_topic"))  # noqa: E731


async def test_session_create_and_unknown_table(client):
    use(FakeLLM())
    r = await client.post("/api/chat/sessions", json={"table_token": await token()})
    assert r.status_code == 201
    body = r.json()
    assert body["table_label"] == "T1" and body["max_message_chars"] == 500
    assert body["turns_remaining"] == 20
    r = await client.post("/api/chat/sessions", json={"table_token": "nope"})
    assert r.status_code == 404 and r.json()["detail"] == "Unknown table"


async def test_sse_frame_order_headers_and_restore(client):
    use(FakeLLM([reply(tc("search_menu", query="", sort="price_asc")), lambda m: _cheap(m)]))
    sid = await start(client)
    r = await client.post(f"/api/chat/sessions/{sid}/messages", json=msg("cheapest?"), headers=SSE)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")
    assert r.headers["cache-control"] == "no-cache" and r.headers["x-accel-buffering"] == "no"
    fr = frames(r.text)
    names = [n for n, _ in fr]
    assert names[0] == "turn" and names[-2:] == ["answer", "done"]
    assert set(names[1:-2]) == {"status"} and len(names) >= 4
    assert fr[0][1]["turn_index"] == 1 and fr[0][1]["request_id"]
    answer = fr[-2][1]["message"]
    assert answer["role"] == "assistant" and answer["id"] > 0
    assert fr[-1][1]["outcome"] == "answered" and fr[-1][1]["turn_id"] == fr[0][1]["turn_id"]

    g = (await client.get(f"/api/chat/sessions/{sid}")).json()
    assert [m["role"] for m in g["messages"]] == ["user", "assistant"]
    assert g["messages"][1]["text"] == answer["text"] and g["turns_remaining"] == 19
    assert (await client.get("/api/chat/sessions/not-a-uuid")).status_code == 404


def _cheap(messages):
    top = json.loads([m for m in messages if m["role"] == "tool"][-1]["content"])["items"][0]
    return reply(respond(f"{top['name']} is {top['price']}.", items=[top["id"]]))


async def test_json_mode_and_debug_flag(client):
    use(FakeLLM([OFF_TOPIC()]))
    sid = await start(client)
    r = await client.post(f"/api/chat/sessions/{sid}/messages", json=msg(), headers=JSON)
    body = r.json()
    assert set(body) == {"turn_id", "outcome", "message"} and body["outcome"] == "off_topic"
    use(FakeLLM([OFF_TOPIC()]), debug=True)
    r = await client.post(f"/api/chat/sessions/{sid}/messages", json=msg(cmid="c-2"), headers=JSON)
    dbg = r.json()["debug"]
    assert dbg["llm_calls"][0]["prompt_tokens"] == 1000 and "messages" not in dbg


async def test_validation_and_404(client):
    use(FakeLLM())
    sid = await start(client)
    url = f"/api/chat/sessions/{sid}/messages"
    assert (await client.post(url, json=msg("   "), headers=JSON)).status_code == 422
    assert (await client.post(url, json=msg("x" * 501), headers=JSON)).status_code == 422
    assert (await client.post(url, json=msg(cmid=""), headers=JSON)).status_code == 422
    bad = "/api/chat/sessions/00000000-0000-0000-0000-000000000000/messages"
    r = await client.post(bad, json=msg(), headers=JSON)
    assert r.status_code == 404 and r.json()["detail"] == "Unknown session"


async def test_409_while_turn_in_flight(client):
    use(FakeLLM([OFF_TOPIC()]))
    sid = await start(client)
    await _sql("UPDATE chat_sessions SET busy_until = now() + interval '1 minute'")
    r = await client.post(f"/api/chat/sessions/{sid}/messages", json=msg(), headers=JSON)
    assert r.status_code == 409 and r.json()["detail"] == "Turn in progress"
    await _sql("UPDATE chat_sessions SET busy_until = now() - interval '1 second'")  # expired
    r = await client.post(f"/api/chat/sessions/{sid}/messages", json=msg(), headers=JSON)
    assert r.status_code == 200


async def _sql(sql, **p):
    from sqlalchemy import text

    from app.db import SessionLocal

    async with SessionLocal() as db:
        await db.execute(text(sql), p)
        await db.commit()


async def test_429_session_limit_table_rate_budget(client):
    llm = FakeLLM([OFF_TOPIC(), OFF_TOPIC(), OFF_TOPIC()])
    use(llm, session_turn_limit=1, table_turns_per_hour=2)
    sid = await start(client)
    url = f"/api/chat/sessions/{sid}/messages"
    assert (await client.post(url, json=msg(cmid="a"), headers=JSON)).status_code == 200
    r = await client.post(url, json=msg(cmid="b"), headers=JSON)
    assert r.status_code == 429 and r.json()["detail"]["reason"] == "session_limit"
    assert r.json()["detail"]["message"]

    sid2 = await start(client)  # same table: 2nd turn of the hour is fine, 3rd is not
    url2 = f"/api/chat/sessions/{sid2}/messages"
    assert (await client.post(url2, json=msg(cmid="c"), headers=JSON)).status_code == 200
    sid3 = await start(client)
    r = await client.post(f"/api/chat/sessions/{sid3}/messages", json=msg(cmid="d"), headers=JSON)
    assert r.status_code == 429 and r.json()["detail"]["reason"] == "table_rate"

    use(FakeLLM([OFF_TOPIC()]), daily_budget_usd=0.0001)
    await _sql(
        "INSERT INTO llm_calls (session_id, turn_id, call_index, model, cost_usd_micros) "
        "SELECT id, gen_random_uuid(), 1, 'deepseek-flash', 500 FROM chat_sessions LIMIT 1"
    )
    sid4 = await start(client, "T2")
    r = await client.post(f"/api/chat/sessions/{sid4}/messages", json=msg(cmid="e"), headers=JSON)
    assert r.status_code == 429 and r.json()["detail"]["reason"] == "budget"


async def test_replay_makes_no_llm_call_and_keeps_turn_count(client):
    llm = FakeLLM([OFF_TOPIC()])
    use(llm)
    sid = await start(client)
    url = f"/api/chat/sessions/{sid}/messages"
    first = (await client.post(url, json=msg(cmid="same"), headers=JSON)).json()
    again = (await client.post(url, json=msg(cmid="same"), headers=JSON)).json()
    assert again == first and len(llm.calls) == 1
    sse = frames((await client.post(url, json=msg(cmid="same"), headers=SSE)).text)
    assert [n for n, _ in sse] == ["turn", "answer", "done"] and len(llm.calls) == 1
    assert (await rows("SELECT turn_count FROM chat_sessions"))[0][0] == 1


async def test_error_turn_retry_reruns_with_same_client_id(client):
    from tests.chat_helpers import llm_error

    llm = FakeLLM([llm_error("server", 503), OFF_TOPIC()])
    use(llm)
    sid = await start(client)
    url = f"/api/chat/sessions/{sid}/messages"
    sse = frames((await client.post(url, json=msg(cmid="r1"), headers=SSE)).text)
    names = [n for n, _ in sse]
    assert names[-2:] == ["error", "done"] and "answer" not in names
    err = sse[-2][1]
    assert (
        err["code"] == "llm_unavailable" and err["fallback"]["actions"][0]["type"] == "call_waiter"
    )
    assert sse[-1][1]["outcome"] == "error"
    r = await client.post(url, json=msg(cmid="r1"), headers=JSON)
    assert r.json()["outcome"] == "off_topic" and len(llm.calls) == 2
    assert (await rows("SELECT turn_count FROM chat_sessions"))[0][0] == 1
    g = (await client.get(f"/api/chat/sessions/{sid}")).json()
    assert [m["role"] for m in g["messages"]] == ["user", "assistant"]


async def _waiter_session(client):
    llm = FakeLLM(
        [
            reply(tc("call_waiter", reason="diner_asked_for_staff", note="the bill please")),
            reply(respond("I can ask a waiter to come over.", "needs_staff")),
        ]
    )
    use(llm)
    sid = await start(client)
    r = await client.post(f"/api/chat/sessions/{sid}/messages", json=msg(), headers=JSON)
    m = r.json()["message"]
    assert r.json()["outcome"] == "escalation_offered"
    return sid, m["actions"][0]


async def test_call_waiter_accept_creates_request_and_event_idempotent(client):
    sid, act = await _waiter_session(client)
    assert act["status"] == "proposed"
    from app.config import get_settings

    rc = aioredis.from_url(get_settings().redis_url)
    ps = rc.pubsub()
    await ps.subscribe(events.CHANNEL_STAFF)
    await ps.get_message(timeout=2)
    url = f"/api/chat/sessions/{sid}/actions/{act['action_id']}"
    r1 = await client.post(url, json={"decision": "accepted"})
    assert r1.status_code == 200
    body = r1.json()
    assert body["action"]["status"] == "accepted"
    wr = body["waiter_request"]
    assert wr["source"] == "chat" and wr["reason"] == "diner_asked_for_staff"
    assert wr["note"] == "the bill please" and wr["session_id"] == sid
    ev = await ps.get_message(ignore_subscribe_messages=True, timeout=2)
    assert ev and json.loads(ev["data"])["type"] == "waiter.requested"
    r2 = await client.post(url, json={"decision": "accepted"})  # idempotent
    assert r2.status_code == 200 and r2.json()["waiter_request"]["id"] == wr["id"]
    assert await ps.get_message(ignore_subscribe_messages=True, timeout=0.3) is None
    assert (await rows("SELECT count(*) FROM waiter_requests"))[0][0] == 1
    flip = await client.post(url, json={"decision": "dismissed"})
    assert flip.status_code == 409 and flip.json()["detail"] == "Action already resolved"
    await ps.aclose()
    await rc.aclose()
    g = (await client.get(f"/api/chat/sessions/{sid}")).json()
    assert g["messages"][-1]["actions"][0]["status"] == "accepted"


async def test_dismiss_and_unknown_action(client):
    sid, act = await _waiter_session(client)
    url = f"/api/chat/sessions/{sid}/actions/{act['action_id']}"
    r = await client.post(url, json={"decision": "dismissed"})
    assert r.json() == {"action": {**act, "status": "dismissed"}, "waiter_request": None}
    assert (await client.post(url, json={"decision": "accepted"})).status_code == 409
    assert (await rows("SELECT count(*) FROM waiter_requests"))[0][0] == 0
    bad = f"/api/chat/sessions/{sid}/actions/00000000-0000-0000-0000-000000000000"
    assert (await client.post(bad, json={"decision": "accepted"})).status_code == 404
    other = await start(client, "T2")
    wrong = f"/api/chat/sessions/{other}/actions/{act['action_id']}"
    assert (await client.post(wrong, json={"decision": "accepted"})).status_code == 404
    assert (await client.post(url, json={"decision": "maybe"})).status_code == 422


async def test_add_to_cart_accept_marks_converted(client):
    holder = {}

    def respond_after(messages):
        return reply(respond("Suggested.", items=[holder["id"]]))

    def add(messages):
        holder["id"] = json.loads([m for m in messages if m["role"] == "tool"][-1]["content"])[
            "items"
        ][0]["id"]
        return reply(tc("add_to_cart", item_id=holder["id"], qty=2))

    use(FakeLLM([reply(tc("search_menu", query="", sort="price_asc")), add, respond_after]))
    sid = await start(client)
    m = (
        await client.post(
            f"/api/chat/sessions/{sid}/messages", json=msg("add the cheapest"), headers=JSON
        )
    ).json()["message"]
    (act,) = m["actions"]
    assert act["type"] == "add_to_cart" and act["qty"] == 2 and act["allergen_conflict"] == []
    r = await client.post(
        f"/api/chat/sessions/{sid}/actions/{act['action_id']}", json={"decision": "accepted"}
    )
    assert r.json()["waiter_request"] is None
    assert (await rows("SELECT converted FROM chat_sessions"))[0][0] is True


async def test_allergen_turn_carries_notice_with_db_items(client):
    holder = {}

    def search_done(messages):
        items = json.loads([m for m in messages if m["role"] == "tool"][-1]["content"])["items"]
        assert items and all("peanut" not in i["allergens"] for i in items)
        holder["i"] = items[0]
        return reply(respond(f"{items[0]['name']} has no peanut listed.", items=[items[0]["id"]]))

    use(
        FakeLLM(
            [
                reply(tc("search_menu", query="", sort="price_asc", exclude_allergens=["peanut"])),
                search_done,
            ]
        )
    )
    sid = await start(client)
    r = await client.post(
        f"/api/chat/sessions/{sid}/messages", json=msg("I am allergic to peanuts"), headers=JSON
    )
    notice = r.json()["message"]["allergen_notice"]
    assert notice["disclaimer"] and notice["items"][0]["menu_item_id"] == holder["i"]["id"]
    assert "peanut" not in notice["items"][0]["allergens"]


async def test_feedback_rules(client):
    use(FakeLLM([OFF_TOPIC()]))
    sid = await start(client)
    await client.post(f"/api/chat/sessions/{sid}/messages", json=msg(), headers=JSON)
    g = (await client.get(f"/api/chat/sessions/{sid}")).json()
    user_m, bot_m = g["messages"]
    assert (
        await client.post(f"/api/chat/messages/{user_m['id']}/feedback", json={"helpful": True})
    ).status_code == 422
    assert (
        await client.post("/api/chat/messages/99999999/feedback", json={"helpful": True})
    ).status_code == 404
    r = await client.post(f"/api/chat/messages/{bot_m['id']}/feedback", json={"helpful": False})
    assert r.status_code == 204 and r.content == b""
    await client.post(
        f"/api/chat/messages/{bot_m['id']}/feedback", json={"helpful": True}
    )  # overwrite
    g = (await client.get(f"/api/chat/sessions/{sid}")).json()
    assert g["messages"][1]["feedback"] is True


async def test_disconnect_cancels_turn_and_releases_busy(client):
    gate = asyncio.Event()

    async def hang(messages):
        await gate.wait()

    deps = use(FakeLLM([hang]))
    sid = await start(client)
    from app.db import SessionLocal

    async with SessionLocal() as db:
        begun = await service.begin_turn(db, deps, __import__("uuid").UUID(sid), "hi", "d1")
    assert (await rows("SELECT busy_until FROM chat_sessions"))[0][0] is not None
    gen = service.stream_turn(deps, begun, "rid-x")
    assert (await gen.__anext__()).startswith(b"event: turn")
    assert b"thinking" in await gen.__anext__()
    await gen.aclose()  # what Starlette does when the client goes away
    for _ in range(40):
        if (await rows("SELECT busy_until FROM chat_sessions"))[0][0] is None:
            break
        await asyncio.sleep(0.05)
    assert (await rows("SELECT busy_until FROM chat_sessions"))[0][0] is None
    assert (await rows("SELECT count(*) FROM chat_messages WHERE role='assistant'"))[0][0] == 0
    # the user can send again (same client id re-runs the turn, no extra turn consumed)
    deps.llm.add(OFF_TOPIC())
    r = await client.post(f"/api/chat/sessions/{sid}/messages", json=msg("hi", "d1"), headers=JSON)
    assert r.status_code == 200 and (await rows("SELECT turn_count FROM chat_sessions"))[0][0] == 1


async def test_request_id_bound_in_sse_generator(client, caplog):
    use(FakeLLM([OFF_TOPIC()]))
    sid = await start(client)
    with caplog.at_level(logging.INFO):
        r = await client.post(
            f"/api/chat/sessions/{sid}/messages",
            json=msg(),
            headers={**SSE, "X-Request-ID": "rid-sse-123"},
        )
    assert frames(r.text)[0][1]["request_id"] == "rid-sse-123"
    done = [
        rec.msg
        for rec in caplog.records
        if isinstance(rec.msg, dict) and rec.msg.get("event") == "chat_turn_done"
    ]
    assert done and done[0]["request_id"] == "rid-sse-123"
    started = [
        rec.msg
        for rec in caplog.records
        if isinstance(rec.msg, dict) and rec.msg.get("event") == "chat_turn_started"
    ]
    assert started and "text" not in started[0] and started[0]["text_len"] == 5
