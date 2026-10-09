import asyncio
import json
import uuid

import pytest
import redis.asyncio as aioredis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app import events
from app.metrics import EVENT_PUBLISH_FAILURES


def key() -> str:
    return f"test-{uuid.uuid4()}"


@pytest.fixture
async def db(seeded_test_db):
    engine = create_async_engine(seeded_test_db)
    yield engine
    await engine.dispose()


async def scalar(db, sql, **params):
    async with db.connect() as conn:
        return (await conn.execute(text(sql), params)).scalar_one()


@pytest.fixture
async def token(db):
    return await scalar(db, "SELECT qr_token FROM dining_tables WHERE label='T1'")


@pytest.fixture
async def items(client):
    menu = (await client.get("/api/menu")).json()
    return {i["name"]: i for c in menu for i in c["items"]}


@pytest.fixture
async def sub():
    from app.config import get_settings
    from tests.conftest import PG  # noqa: F401  (ensures env is set)

    r = aioredis.from_url(get_settings().redis_url)
    ps = r.pubsub()
    await ps.subscribe(events.CHANNEL_NEW, events.CHANNEL_STATUS)
    for _ in range(2):  # wait for both subscribe confirmations
        await ps.get_message(timeout=2)
    yield ps
    await ps.aclose()
    await r.aclose()


async def next_event(ps, wait=2.0):
    msg = await ps.get_message(ignore_subscribe_messages=True, timeout=wait)
    return None if msg is None else (msg["channel"].decode(), json.loads(msg["data"]))


def body(token, lines, **extra):
    return {
        "table_token": token,
        "items": [{"menu_item_id": i, "qty": q} for i, q in lines],
        **extra,
    }


# ---------- tables and menu ----------


async def test_table_lookup_and_404(client, token):
    r = await client.get(f"/api/tables/{token}")
    assert r.status_code == 200 and r.json()["label"] == "T1"
    assert (await client.get("/api/tables/nope")).status_code == 404


async def test_menu_peanut_exclusion(client):
    names = {i["name"] for c in (await client.get("/api/menu")).json() for i in c["items"]}
    assert "Chicken Satay" in names
    r = await client.get("/api/menu", params={"exclude_allergens": "peanut"})
    filtered = {i["name"] for c in r.json() for i in c["items"]}
    assert "Chicken Satay" not in filtered and filtered
    for c in r.json():
        for i in c["items"]:
            assert "peanut" not in i["allergens"]


async def test_menu_vegan_filter_requires_all_tags(client):
    r = await client.get("/api/menu", params={"dietary": "vegan,gluten_free"})
    items = [i for c in r.json() for i in c["items"]]
    assert items
    assert all({"vegan", "gluten_free"} <= set(i["dietary_tags"]) for i in items)


async def test_menu_unknown_values_422(client):
    r = await client.get("/api/menu", params={"exclude_allergens": "peanut,kryptonite"})
    assert r.status_code == 422 and r.json()["detail"]["invalid"] == ["kryptonite"]
    assert (await client.get("/api/menu", params={"dietary": "paleo"})).status_code == 422


async def test_menu_item_get(client, items):
    i = items["Chicken Satay"]
    r = await client.get(f"/api/menu/items/{i['id']}")
    assert r.status_code == 200 and "peanut" in r.json()["allergens"]
    assert (await client.get("/api/menu/items/999999")).status_code == 404


# ---------- create order ----------


async def test_create_order_prices_from_db(client, token, items, db):
    satay, lemon = items["Chicken Satay"], items["Fresh Lemonade"]
    r = await client.post(
        "/api/orders",
        json=body(token, [(satay["id"], 2), (lemon["id"], 1)]),
        headers={"Idempotency-Key": key()},
    )
    assert r.status_code == 201, r.text
    o = r.json()
    assert o["total_cents"] == 2 * satay["price_cents"] + lemon["price_cents"]
    assert o["status"] == "received" and o["table_label"] == "T1"
    assert [i["item_name"] for i in o["items"]] == ["Chicken Satay", "Fresh Lemonade"]
    assert o["items"][0]["unit_price_cents"] == satay["price_cents"]
    got = await client.get(f"/api/orders/{o['public_id']}")
    assert got.status_code == 200 and got.json() == o


async def test_client_sent_price_rejected(client, token, items):
    b = body(token, [(items["Chicken Satay"]["id"], 1)])
    b["items"][0]["unit_price_cents"] = 1
    r = await client.post("/api/orders", json=b, headers={"Idempotency-Key": key()})
    assert r.status_code == 422
    b2 = body(token, [(items["Chicken Satay"]["id"], 1)], total_cents=1)
    assert (
        await client.post("/api/orders", json=b2, headers={"Idempotency-Key": key()})
    ).status_code == 422


@pytest.mark.parametrize("k", [None, "short", "x" * 129])
async def test_idempotency_key_required(client, token, items, k):
    h = {} if k is None else {"Idempotency-Key": k}
    r = await client.post("/api/orders", json=body(token, [(items["Edamame"]["id"], 1)]), headers=h)
    assert r.status_code == 400


async def test_bad_quantities_and_empty(client, token, items):
    eid = items["Edamame"]["id"]
    for lines in ([], [(eid, 0)], [(eid, 21)]):
        r = await client.post(
            "/api/orders", json=body(token, lines), headers={"Idempotency-Key": key()}
        )
        assert r.status_code == 422


async def test_unknown_table_404(client, items):
    r = await client.post(
        "/api/orders",
        json=body("nope", [(items["Edamame"]["id"], 1)]),
        headers={"Idempotency-Key": key()},
    )
    assert r.status_code == 404


async def test_unavailable_or_missing_item_422(client, token, items, db):
    eid = items["Edamame"]["id"]
    async with db.begin() as conn:
        await conn.execute(text("UPDATE menu_items SET is_available=false WHERE id=:i"), {"i": eid})
    try:
        r = await client.post(
            "/api/orders",
            json=body(token, [(eid, 1), (999999, 1)]),
            headers={"Idempotency-Key": key()},
        )
    finally:
        async with db.begin() as conn:
            await conn.execute(
                text("UPDATE menu_items SET is_available=true WHERE id=:i"), {"i": eid}
            )
    assert r.status_code == 422
    d = r.json()["detail"]
    assert d["unavailable_item_ids"] == [eid] and d["missing_item_ids"] == [999999]


# ---------- idempotency ----------


async def test_idempotent_replay_same_order(client, token, items, db):
    k = key()
    b = body(token, [(items["Edamame"]["id"], 1)], notes="hello")
    r1 = await client.post("/api/orders", json=b, headers={"Idempotency-Key": k})
    r2 = await client.post("/api/orders", json=b, headers={"Idempotency-Key": k})
    assert r1.status_code == 201 and r2.status_code == 200
    assert r2.headers["idempotent-replay"] == "true" and "idempotent-replay" not in r1.headers
    assert r1.json() == r2.json()
    assert await scalar(db, "SELECT count(*) FROM orders WHERE idempotency_key=:k", k=k) == 1


async def test_same_key_different_body_409(client, token, items):
    k = key()
    r1 = await client.post(
        "/api/orders",
        json=body(token, [(items["Edamame"]["id"], 1)]),
        headers={"Idempotency-Key": k},
    )
    r2 = await client.post(
        "/api/orders",
        json=body(token, [(items["Edamame"]["id"], 2)]),
        headers={"Idempotency-Key": k},
    )
    assert r1.status_code == 201 and r2.status_code == 409


async def test_concurrent_identical_requests_one_order(client, token, items, db, sub):
    k = key()
    b = body(token, [(items["Edamame"]["id"], 3)])
    rs = await asyncio.gather(
        *[client.post("/api/orders", json=b, headers={"Idempotency-Key": k}) for _ in range(10)]
    )
    codes = sorted(r.status_code for r in rs)
    assert codes == [200] * 9 + [201], codes
    assert len({r.json()["public_id"] for r in rs}) == 1
    assert await scalar(db, "SELECT count(*) FROM orders WHERE idempotency_key=:k", k=k) == 1
    assert (
        await scalar(
            db,
            "SELECT count(*) FROM order_items oi JOIN orders o ON o.id=oi.order_id "
            "WHERE o.idempotency_key=:k",
            k=k,
        )
        == 1
    )
    # exactly one event despite 10 requests
    assert (await next_event(sub)) is not None
    assert (await next_event(sub, wait=0.3)) is None


# ---------- get order ----------


async def test_get_order_unknown_404(client):
    assert (await client.get(f"/api/orders/{uuid.uuid4()}")).status_code == 404
    assert (await client.get("/api/orders/123")).status_code == 422  # ids are not enumerable


# ---------- events ----------


async def test_order_created_event_published_after_commit(client, token, items, sub):
    r = await client.post(
        "/api/orders",
        json=body(token, [(items["Edamame"]["id"], 1)]),
        headers={"Idempotency-Key": key(), "X-Request-ID": "req-evt-1"},
    )
    ch, ev = await next_event(sub)
    assert ch == "orders.new"
    assert ev["type"] == "order.created" and ev["version"] == 1
    assert ev["request_id"] == "req-evt-1"
    assert isinstance(ev["published_at_us"], int) and ev["published_at_us"] > 1.7e15
    assert ev["order"] == r.json()


async def test_replay_publishes_nothing(client, token, items, sub):
    k = key()
    b = body(token, [(items["Edamame"]["id"], 1)])
    await client.post("/api/orders", json=b, headers={"Idempotency-Key": k})
    await next_event(sub)
    await client.post("/api/orders", json=b, headers={"Idempotency-Key": k})
    assert await next_event(sub, wait=0.3) is None


async def test_commit_failure_publishes_nothing(client, token, items, db, sub, monkeypatch):
    async def boom(self):
        raise RuntimeError("commit failed")

    monkeypatch.setattr(AsyncSession, "commit", boom)
    k = key()
    with pytest.raises(RuntimeError):
        await client.post(
            "/api/orders",
            json=body(token, [(items["Edamame"]["id"], 1)]),
            headers={"Idempotency-Key": k},
        )
    monkeypatch.undo()
    assert await next_event(sub, wait=0.3) is None
    assert await scalar(db, "SELECT count(*) FROM orders WHERE idempotency_key=:k", k=k) == 0


async def test_redis_down_still_201_and_counts_failure(client, token, items, monkeypatch):
    dead = aioredis.from_url("redis://localhost:1/0", socket_connect_timeout=0.2)
    monkeypatch.setattr(events, "_client", dead)
    before = EVENT_PUBLISH_FAILURES.labels("orders.new")._value.get()
    r = await client.post(
        "/api/orders",
        json=body(token, [(items["Edamame"]["id"], 1)]),
        headers={"Idempotency-Key": key()},
    )
    assert r.status_code == 201
    assert EVENT_PUBLISH_FAILURES.labels("orders.new")._value.get() == before + 1
    await dead.aclose()


# ---------- kitchen ----------


async def make_order(client, token, items):
    r = await client.post(
        "/api/orders",
        json=body(token, [(items["Edamame"]["id"], 1)]),
        headers={"Idempotency-Key": key()},
    )
    return r.json()


async def test_status_chain_and_events(client, token, items, sub):
    o = await make_order(client, token, items)
    await next_event(sub)
    for target in ("preparing", "ready", "served"):
        r = await client.patch(f"/api/kitchen/orders/{o['id']}/status", json={"status": target})
        assert r.status_code == 200 and r.json()["status"] == target
        ch, ev = await next_event(sub)
        assert ch == "orders.status" and ev["type"] == "order.status_changed"
        assert ev["order"]["status"] == target and ev["order"]["public_id"] == o["public_id"]
    got = (await client.get(f"/api/orders/{o['public_id']}")).json()
    assert got["status"] == "served" and got["updated_at"] > o["updated_at"]


async def test_invalid_transitions_409(client, token, items):
    o = await make_order(client, token, items)
    url = f"/api/kitchen/orders/{o['id']}/status"
    assert (await client.patch(url, json={"status": "served"})).status_code == 409  # skip
    assert (await client.patch(url, json={"status": "received"})).status_code == 409  # same
    assert (await client.patch(url, json={"status": "cancelled"})).status_code == 200
    assert (await client.patch(url, json={"status": "preparing"})).status_code == 409  # terminal
    assert (await client.patch(url, json={"status": "bogus"})).status_code == 422
    assert (
        await client.patch("/api/kitchen/orders/999999999/status", json={"status": "preparing"})
    ).status_code == 404


async def test_concurrent_double_transition_one_wins(client, token, items):
    o = await make_order(client, token, items)
    url = f"/api/kitchen/orders/{o['id']}/status"
    rs = await asyncio.gather(*[client.patch(url, json={"status": "preparing"}) for _ in range(2)])
    assert sorted(r.status_code for r in rs) == [200, 409]


async def test_kitchen_list_active_oldest_first(client, token, items):
    a = await make_order(client, token, items)
    b = await make_order(client, token, items)
    await client.patch(f"/api/kitchen/orders/{b['id']}/status", json={"status": "cancelled"})
    r = await client.get("/api/kitchen/orders")
    assert r.status_code == 200
    ids = [o["id"] for o in r.json()]
    assert a["id"] in ids and b["id"] not in ids
    assert ids == sorted(ids)
    assert all(o["status"] in ("received", "preparing", "ready") for o in r.json())
    cancelled = await client.get("/api/kitchen/orders", params={"status": "cancelled"})
    assert b["id"] in [o["id"] for o in cancelled.json()]
    assert (await client.get("/api/kitchen/orders", params={"status": "x"})).status_code == 422
