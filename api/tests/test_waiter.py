import asyncio
import json

import pytest
import redis.asyncio as aioredis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app import events
from app.metrics import EVENT_PUBLISH_FAILURES


@pytest.fixture
async def db(seeded_test_db):
    engine = create_async_engine(seeded_test_db)
    yield engine
    await engine.dispose()


@pytest.fixture(autouse=True)
async def clean(db):
    async with db.begin() as conn:
        await conn.execute(text("DELETE FROM waiter_requests"))
    yield
    async with db.begin() as conn:
        await conn.execute(text("DELETE FROM waiter_requests"))


async def token_for(db, label: str) -> str:
    async with db.connect() as conn:
        return (
            await conn.execute(
                text("SELECT qr_token FROM dining_tables WHERE label=:l"), {"l": label}
            )
        ).scalar_one()


@pytest.fixture
async def token(db):
    return await token_for(db, "T1")


@pytest.fixture
async def sub():
    from app.config import get_settings

    r = aioredis.from_url(get_settings().redis_url)
    ps = r.pubsub()
    await ps.subscribe(events.CHANNEL_STAFF)
    await ps.get_message(timeout=2)
    yield ps
    await ps.aclose()
    await r.aclose()


async def next_event(ps, wait=2.0):
    msg = await ps.get_message(ignore_subscribe_messages=True, timeout=wait)
    return None if msg is None else (msg["channel"].decode(), json.loads(msg["data"]))


def req(token, **extra):
    return {"table_token": token, "reason": "diner_asked_for_staff", **extra}


async def test_create_publishes_after_commit(client, token, sub, db):
    r = await client.post("/api/waiter-requests", json=req(token, note="more water"))
    assert r.status_code == 201
    wr = r.json()
    assert wr["table_label"] == "T1" and wr["status"] == "open" and wr["source"] == "button"
    assert wr["note"] == "more water" and wr["session_id"] is None
    assert wr["created_at"].endswith("Z") or wr["created_at"].endswith("+00:00")

    channel, env = await next_event(sub)
    assert channel == "staff.requests"
    assert env["type"] == "waiter.requested" and env["version"] == 1
    assert env["waiter_request"] == wr
    assert env["request_id"] and env["published_at_us"] > 0
    # The row the event describes is already committed and visible to another connection.
    async with db.connect() as conn:
        n = (
            await conn.execute(
                text("SELECT count(*) FROM waiter_requests WHERE id=:i"),
                {"i": env["waiter_request"]["id"]},
            )
        ).scalar_one()
    assert n == 1


async def test_commit_failure_publishes_nothing(client, token, sub, monkeypatch):
    async def boom(self):
        raise RuntimeError("commit failed")

    monkeypatch.setattr(AsyncSession, "commit", boom)
    with pytest.raises(RuntimeError):
        await client.post("/api/waiter-requests", json=req(token))
    monkeypatch.undo()
    assert await next_event(sub, wait=0.3) is None


async def test_second_open_request_returns_existing(client, token, sub):
    first = await client.post("/api/waiter-requests", json=req(token))
    second = await client.post(
        "/api/waiter-requests",
        json={"table_token": token, "reason": "other", "note": "different"},
    )
    assert first.status_code == 201 and second.status_code == 200
    assert second.json() == first.json()
    assert (await next_event(sub))[1]["type"] == "waiter.requested"
    assert await next_event(sub, wait=0.3) is None  # no event for the existing one


async def test_concurrent_creates_make_one_row(client, token, db):
    rs = await asyncio.gather(
        *[client.post("/api/waiter-requests", json=req(token)) for _ in range(8)]
    )
    assert sorted(r.status_code for r in rs) == [200] * 7 + [201]
    assert len({r.json()["id"] for r in rs}) == 1
    async with db.connect() as conn:
        n = (await conn.execute(text("SELECT count(*) FROM waiter_requests"))).scalar_one()
    assert n == 1


async def test_new_request_allowed_after_acknowledge(client, token):
    a = (await client.post("/api/waiter-requests", json=req(token))).json()
    await client.patch(f"/api/waiter-requests/{a['id']}", json={"status": "acknowledged"})
    b = await client.post("/api/waiter-requests", json=req(token))
    assert b.status_code == 201 and b.json()["id"] != a["id"]


async def test_unknown_table_and_validation(client, token):
    r = await client.post("/api/waiter-requests", json=req("nope"))
    assert r.status_code == 404 and r.json() == {"detail": "Unknown table"}
    assert (
        await client.post("/api/waiter-requests", json={"table_token": token, "reason": "x"})
    ).status_code == 422
    assert (
        await client.post("/api/waiter-requests", json=req(token, note="n" * 201))
    ).status_code == 422


async def test_list_open_oldest_first_and_status_filter(client, db):
    t1, t2 = await token_for(db, "T1"), await token_for(db, "T2")
    a = (await client.post("/api/waiter-requests", json=req(t1))).json()
    b = (await client.post("/api/waiter-requests", json=req(t2))).json()
    await client.patch(f"/api/waiter-requests/{b['id']}", json={"status": "acknowledged"})
    open_ = (await client.get("/api/waiter-requests")).json()
    assert [i["id"] for i in open_["items"]] == [a["id"]]
    ack = (await client.get("/api/waiter-requests", params={"status": "acknowledged"})).json()
    assert [i["id"] for i in ack["items"]] == [b["id"]]
    assert (await client.get("/api/waiter-requests", params={"status": "bogus"})).status_code == 422


async def test_patch_transitions_and_event(client, token, sub):
    wr = (await client.post("/api/waiter-requests", json=req(token))).json()
    await next_event(sub)
    url = f"/api/waiter-requests/{wr['id']}"

    r = await client.patch(url, json={"status": "acknowledged"})
    assert r.status_code == 200 and r.json()["status"] == "acknowledged"
    _, env = await next_event(sub)
    assert env["type"] == "waiter.updated" and env["waiter_request"] == r.json()

    # acknowledged -> acknowledged is invalid; nothing published
    r = await client.patch(url, json={"status": "acknowledged"})
    assert r.status_code == 409 and r.json() == {"detail": "Invalid transition"}
    assert await next_event(sub, wait=0.3) is None

    r = await client.patch(url, json={"status": "closed"})
    assert r.status_code == 200 and r.json()["status"] == "closed"
    assert (await next_event(sub))[1]["waiter_request"]["status"] == "closed"

    r = await client.patch(url, json={"status": "closed"})
    assert r.status_code == 409


async def test_open_can_close_directly_and_patch_errors(client, token):
    wr = (await client.post("/api/waiter-requests", json=req(token))).json()
    r = await client.patch(f"/api/waiter-requests/{wr['id']}", json={"status": "closed"})
    assert r.status_code == 200
    r = await client.patch("/api/waiter-requests/999999999", json={"status": "closed"})
    assert r.status_code == 404 and r.json() == {"detail": "Unknown waiter request"}
    r = await client.patch(f"/api/waiter-requests/{wr['id']}", json={"status": "open"})
    assert r.status_code == 422


async def test_service_function_is_uncommitted_and_announce_after_commit(db, token, sub):
    """Contract for WP3: create_waiter_request does not commit; announce() publishes after."""
    from app import waiter as svc
    from app.db import SessionLocal

    async with SessionLocal() as s:
        tid = (
            await s.execute(text("SELECT id FROM dining_tables WHERE qr_token=:t"), {"t": token})
        ).scalar_one()
        wr, created = await svc.create_waiter_request(
            s, tid, "allergy_or_dietary", "peanut", "chat"
        )
        assert created and wr.source == "chat"
        # not committed yet: invisible to another connection, nothing published
        async with db.connect() as conn:
            assert (
                await conn.execute(text("SELECT count(*) FROM waiter_requests"))
            ).scalar_one() == 0
        assert await next_event(sub, wait=0.2) is None
        wr2, created2 = await svc.create_waiter_request(s, tid, "other", None, "chat")
        assert not created2 and wr2.id == wr.id
        await s.commit()
        await svc.announce(wr, created)
        await svc.announce(wr2, created2)  # no-op
    assert (await next_event(sub))[1]["waiter_request"]["reason"] == "allergy_or_dietary"
    assert await next_event(sub, wait=0.2) is None


async def test_publish_failure_is_counted_not_raised(client, token, monkeypatch):
    def broken():
        raise ConnectionError("redis down")

    monkeypatch.setattr(events, "get_redis", broken)
    before = EVENT_PUBLISH_FAILURES.labels(events.CHANNEL_STAFF)._value.get()
    r = await client.post("/api/waiter-requests", json=req(token))
    assert r.status_code == 201
    assert EVENT_PUBLISH_FAILURES.labels(events.CHANNEL_STAFF)._value.get() == before + 1
