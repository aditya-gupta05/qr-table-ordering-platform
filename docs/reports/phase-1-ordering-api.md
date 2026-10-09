# Phase 1: Ordering API

## Summary
Phase 1 turns the Phase 0 skeleton into a working ordering backend: menu and table lookup, order creation with server-side
pricing and concurrency-safe idempotency, a kitchen queue with a guarded status state machine, and Redis events published
strictly after commit. The Go service does not consume the events yet (Phase 2); the wire format is fixed in
[docs/contracts/events.md](../contracts/events.md).

- Migration: [0002_order_public_id.py](../../api/alembic/versions/0002_order_public_id.py)
- Routers: [menu.py](../../api/app/routers/menu.py), [orders.py](../../api/app/routers/orders.py), [kitchen.py](../../api/app/routers/kitchen.py)
- Support: [schemas.py](../../api/app/schemas.py), [events.py](../../api/app/events.py), [metrics.py](../../api/app/metrics.py)
- Tests: [test_orders.py](../../api/tests/test_orders.py) (25 new, 33 total, all against real Postgres `qr_test` and Redis db 15)
- Bench: [bench/order_post_latency.py](../../bench/order_post_latency.py), output in [bench/results/](../../bench/results/phase1-post-orders.txt)

Endpoints (all under `/api`, matching the Vite proxy prefix, which forwards `/api` without rewriting):

| Method and path | Purpose |
|---|---|
| `GET /api/tables/{qr_token}` | Resolve a QR token to `{id, label}`; 404 if unknown |
| `GET /api/menu?exclude_allergens=&dietary=` | Available items by category; filters validated against `vocab.py` (422) |
| `GET /api/menu/items/{id}` | One item |
| `POST /api/orders` | Create order; `Idempotency-Key` header required; 201 new, 200 replay, 409 key reuse with other body |
| `GET /api/orders/{public_id}` | Diner order lookup by UUID |
| `GET /api/kitchen/orders?status=` | Active orders (default received, preparing, ready), oldest first |
| `PATCH /api/kitchen/orders/{id}/status` | State machine transition; 409 if invalid or lost a race |

Known gap, stated on purpose: kitchen endpoints have **no authentication** (staff auth is out of scope in `docs/PLAN.md`).
Anyone who can reach the API can move orders. A real deployment needs at least a shared secret or staff login.

## 1. Technologies, languages, libraries, frameworks

**Postgres `INSERT ... ON CONFLICT DO NOTHING RETURNING` (via SQLAlchemy `postgresql.insert`).** The idempotency arbiter. The
UNIQUE index on `idempotency_key` decides who wins, atomically, inside the database. Alternative: `SELECT` first, then `INSERT`.
That has a time-of-check/time-of-use race: two concurrent requests both see "no row" and both insert (one fails with a 500, or
worse, there is no unique index and both succeed). Alternative 2: an application-level lock or Redis `SETNX`; it adds a second
source of truth that can disagree with the DB.

**Compare-and-set `UPDATE ... WHERE id=:id AND status=:current`.** Guards the kitchen state machine. Alternative:
`SELECT ... FOR UPDATE` then update. Both are correct; CAS needs no explicit lock held across a round trip, and the "0 rows
updated" result maps directly to a 409.

**`uuid` public ids with `gen_random_uuid()`.** Built into Postgres 13+, so no extension. Alternative: client-visible bigserial ids
(enumerable, see decisions below) or ULIDs (sortable, but we don't need sortability for diners).

**Pydantic v2 `extra="forbid"`.** Request models reject unknown fields, so a client-sent `unit_price_cents` or `total_cents` is a 422
rather than silently ignored. Silent ignoring is the dangerous behaviour: a client developer believes the price they sent was honoured.

**sha256 request hash.** Canonical JSON (`sort_keys`, compact separators) of the validated body. Alternative: compare the stored
rows to the incoming request, which is more code and fragile when menu prices change between the original and the retry.

**redis-py asyncio (`PUBLISH`) as the event bus.** One lazily created shared client ([events.py](../../api/app/events.py)), closed in the
app lifespan. Alternatives: Redis Streams (persistent, consumer groups, replay) or Postgres `LISTEN/NOTIFY`. Pub/sub was chosen because
the Phase 2 consumer is a single always-on hub, the DB is the source of truth, and pub/sub is the lowest-latency option. Streams would
be the first upgrade if at-least-once delivery mattered.

**prometheus-client business metrics** ([metrics.py](../../api/app/metrics.py)): `orders_created_total`, `order_status_changes_total{to}`,
`order_create_seconds`, `order_event_publish_seconds`, `order_events_publish_failures_total{channel}`. Labels are low-cardinality
(a fixed five-value enum, two channel names).

**pytest-asyncio with real Redis pub/sub in tests.** The tests subscribe to `orders.new` / `orders.status` themselves and assert the
actual published envelope, which proves the contract and the publish-after-commit ordering rather than mocking `publish`.

**ruff `extend-immutable-calls`.** Tells bugbear that `fastapi.Depends/Query/Header` in argument defaults are fine (see challenge 1).

## 2. Technical challenges and how they were approached

Phase 1 also had no dead ends worth the name; the problems below were found by the linter or the first test run. No escalation was needed.

1. **Ruff B008 on every FastAPI route.** Problem: `session: AsyncSession = Depends(get_session)` triggered "do not perform function
   call in argument defaults" (7 errors). Cause: bugbear does not know FastAPI's `Depends` returns an immutable marker evaluated once at
   import. Fix: `extend-immutable-calls = ["fastapi.Depends", "fastapi.Query", "fastapi.Header"]` in `pyproject.toml`. Lesson: configure
   the rule for the framework; do not blanket-disable B008 and do not switch to `Annotated` just to satisfy a linter.

2. **`'Insert' object has no attribute 'on_conflict_do_nothing'`.** Cause: I imported the generic `sqlalchemy.insert`; upsert
   methods exist only on the dialect-specific `sqlalchemy.dialects.postgresql.insert` (the seed already did this correctly). Fix: import
   both (`pg_insert` for the order, `core_insert` for the bulk item insert). All 12 order tests failed on this one line, then went green together.

3. **ASYNC109 in the test helper** (`async def next_event(ps, timeout=...)`). Cause: ruff wants `asyncio.timeout` for async functions with
   timeout parameters. Fix: renamed the parameter to `wait`, since it is passed straight to redis-py's own `get_message(timeout=...)`.

4. **Making idempotency safe under concurrency (the important one).** Approach: no read-before-write. The transaction is
   `INSERT order ... ON CONFLICT (idempotency_key) DO NOTHING RETURNING id`. If a row comes back we won and insert the items; if not, we load
   the existing order. Why this is safe: a concurrent identical request's INSERT blocks on the unique index until the first transaction commits
   or rolls back; after commit it gets DO NOTHING and, under READ COMMITTED, its next statement sees the committed order and its items
   (both were committed together). Evidence: `test_concurrent_identical_requests_one_order` fires 10 identical requests with
   `asyncio.gather` and asserts status codes are exactly nine 200s and one 201, one distinct `public_id`, one `orders` row, one `order_items`
   row, and exactly one event on `orders.new`. Ran the whole suite 4 times with no flake.

5. **Detecting key reuse with a different payload.** A replay with the same key but different items must not return the first order as if it
   satisfied the new request. Solution: store `request_hash` (sha256 of canonical JSON) and compare; mismatch is 409. Evidence:
   `test_same_key_different_body_409`, and the curl walkthrough below (qty 2 vs 3 returned 409).

6. **Publish strictly after commit, and prove it.** The order of operations in `create_order` is: insert, insert items, `await
   session.commit()`, build the response from a fresh read, then `events.publish`. Proof: `test_commit_failure_publishes_nothing`
   monkeypatches `AsyncSession.commit` to raise, asserts the exception, that nothing arrives on `orders.new`, and that zero rows exist for the key.
   The reverse failure is covered too: `test_redis_down_still_201_and_counts_failure` points the client at a dead port; the response is still
   201 and `order_events_publish_failures_total{channel="orders.new"}` increases by exactly 1.

7. **Stale ORM state after core DML.** Problem: statuses are changed with a core `UPDATE`, so any `Order` instance already in the session's
   identity map would show the old status. Fix: status checks read just the column (not the entity), and `load_order` uses
   `populate_existing()` so the response and the event always reflect the committed row. `expire_on_commit=False` (set in Phase 0) means we must
   be explicit about this.

8. **Concurrent kitchen clicks.** Two simultaneous `received -> preparing` requests both pass the in-memory transition check. The CAS update
   (`WHERE status = :current`) lets exactly one succeed; the other updates 0 rows and returns 409. Evidence: `test_concurrent_double_transition_one_wins`
   asserts `[200, 409]`. A subtle point: the loser calls `session.rollback()` before raising so the connection returns clean to the pool.

9. **Not leaking enumerable ids.** `GET /api/orders/{public_id}` is typed as `uuid.UUID`, so `/api/orders/123` is a 422 (tested), and a diner
   cannot walk through other tables' orders. Honest limitation: the order JSON still contains the bigserial `id` (the kitchen board needs it
   for PATCH and the contract says the event shape equals the REST shape). That is harmless for lookup, since the diner endpoint ignores it, but it
   does reveal order volume. A later hardening step is to give kitchen endpoints their own shape or to switch them to `public_id` too.

10. **Test data hygiene.** The dev DB keeps one walkthrough order. The 200 benchmark orders were tagged `notes='bench-phase1'` and deleted after the run so
    they would not clutter the Phase 2 kitchen board. A fixture in the tests temporarily marks an item unavailable and restores it in `finally`, so the
    shared `qr_test` seed stays valid for later tests.

11. **My own curl typo, not a product bug.** The first "client-sent price" walkthrough request returned a JSON decode error (422) because bash
    string substitution mangled the body. Redone with a clean literal body it returned the intended
    `extra_forbidden ... unit_price_cents` error. Worth remembering: check that a 422 is the *right* 422.

### Design decisions worth defending
- **`public_id` UUID vs bigserial in diner URLs.** Sequential ids let anyone iterate `/orders/1..N` and read other tables' orders (names,
  notes, totals). UUIDv4 has 122 random bits. The bigserial `id` stays as the internal primary key and for the kitchen.
- **Migration 0002 adds `request_hash NOT NULL` with a temporary default.** Existing rows (none yet) would otherwise block the `NOT NULL`;
  the default is removed immediately so new code can never forget to set it.
- **Server-side pricing.** Totals are computed from DB prices at request time and snapshotted into `order_items`; the client cannot influence them.
- **Duplicate lines are not merged.** Two lines with the same item can carry different notes ("no ice" vs "extra ice"). The kitchen sees what the diner typed.
- **Errors carry actionable detail.** 422 for unavailable items returns `missing_item_ids` and `unavailable_item_ids`, so the UI can remove exactly those lines.
- **Replay returns 200 with `Idempotent-Replay: true`, no event.** The kitchen must not see the same order twice because a phone retried on bad Wi-Fi.
- **State machine as data.** `TRANSITIONS` is a dict: received to preparing/cancelled; preparing to ready/cancelled; ready to served; served and cancelled terminal.
  Skips (`received -> served`) and same-state requests are 409.
- **Event failures never fail the order.** The diner's order is durable; telling them "error" would cause a retry (safe thanks to idempotency, but
  confusing). The kitchen board recovers by calling `GET /api/kitchen/orders` on connect/reconnect.

### Transactional outbox: the stronger alternative we deferred
The current gap: if the API process dies between `COMMIT` and `PUBLISH`, the order exists but no event is ever sent. The outbox pattern
closes it: in the same transaction as the order, insert a row into an `outbox` table; a separate relay reads unpublished rows, publishes, and marks them sent
(or uses `LISTEN/NOTIFY` / logical replication). Properties: events are never lost (at-least-once), at the cost of possible duplicates, so consumers must
de-duplicate on an event id; extra table, extra worker, extra latency (polling interval or notification hop) on the exact path we want under 100 ms.
Why deferred: (a) Redis pub/sub is itself fire-and-forget, so a durable outbox in front of a lossy channel only protects half the chain; (b) the
recovery path (REST refetch on reconnect) already exists and is cheap; (c) the failure window is the microseconds between commit and publish. If we later need
guaranteed delivery, the order would be outbox + Redis Streams with consumer acknowledgements, not just an outbox.

## 3. Deployment, automation, monitoring, logging

- **Schema:** migration 0002 runs automatically in the API container entrypoint (`alembic upgrade head`). Verified `alembic_version = 0002` after rebuild.
- **New metrics** on the existing `/metrics`: `orders_created_total`, `order_status_changes_total{to}`, `order_create_seconds` (buckets 2 ms to 1 s),
  `order_event_publish_seconds` (0.5 ms to 500 ms), `order_events_publish_failures_total{channel}`. After the benchmark run the scrape showed
  `orders_created_total 201`, `order_create_seconds_sum 0.346 s / count 201` (about 1.7 ms mean inside the handler), and `order_event_publish_seconds_sum 0.032 s /
  count 204` (about 0.16 ms mean per PUBLISH).
- **Logs:** new structured events `order_created` (order_id, table, total_cents), `order_status_changed` (order_id, from_status, to) and `order_event_publish_failed`
  (channel, order_id, event_type, error). All carry `request_id` automatically through the contextvars bound in Phase 0. Real line:
  `{"order_id": 1, "from_status": "received", "to": "preparing", "event": "order_status_changed", "request_id": "df05e233-...", "logger": "kitchen", ...}`.
  The same `request_id` is placed in the event envelope, so Phase 2 can log it in Go and complete the trace API -> Redis -> hub -> WebSocket.
- **Contract doc:** [docs/contracts/events.md](../contracts/events.md) defines channels, envelope, and the rule that consumers ignore unknown fields. `published_at_us`
  (epoch microseconds from `time.time_ns() // 1000`) is taken right before PUBLISH and is what the Phase 3 benchmark will subtract from the client receive time.
- **Not done:** no Grafana panels for the new metrics yet (Phase 3), no auth on kitchen endpoints, no CI.

### Curl walkthrough (live stack, abbreviated)
Event capture in the background: `docker compose exec -T redis redis-cli SUBSCRIBE orders.new orders.status`.

```text
$ TOKEN=$(docker compose exec -T postgres psql -U qr -d qr -tAc "select qr_token from dining_tables where label='T3'")
$ curl -s localhost:8000/api/tables/$TOKEN
{"id":3,"label":"T3"}

$ curl -s "localhost:8000/api/menu?exclude_allergens=peanut&dietary=vegan"     # satay absent, only vegan items
[('Starters', [4, 6]), ('Bowls & Curries', [10, 12]), ('Sides', [22, 24, 25]), ('Desserts', [29, 31]), ('Drinks', [32, 34, 35])]

$ curl -si -H "Idempotency-Key: walk-1" -H 'Content-Type: application/json' -d '{"table_token":"...","items":[{"menu_item_id":1,"qty":2,"notes":"extra sauce"},{"menu_item_id":4,"qty":1}]}' localhost:8000/api/orders
HTTP/1.1 201 Created
{'id': 1, 'public_id': 'c414ba9b-eec9-45fd-bb98-6e22ea92e919', 'table_label': 'T3', 'status': 'received', 'total_cents': 3000}   # 2*1150 + 700

$ (same request again, same key)
HTTP/1.1 200 OK
idempotent-replay: true

$ (same key, qty 3 instead of 2)
{"detail":"Idempotency-Key was already used with a different request"} [409]

$ (item with "unit_price_cents":1)
{"detail":[{"type":"extra_forbidden","loc":["body","items",0,"unit_price_cents"],"msg":"Extra inputs are not permitted","input":1}]} [422]

$ curl -s localhost:8000/api/kitchen/orders
kitchen active: [(1, 'T3', 'received')]

$ for s in preparing ready served; do curl -s -X PATCH -H 'Content-Type: application/json' -d "{\"status\":\"$s\"}" localhost:8000/api/kitchen/orders/1/status; done
-> preparing
-> ready
-> served

$ curl -s -X PATCH ... -d '{"status":"preparing"}' localhost:8000/api/kitchen/orders/1/status
{"detail":"cannot move order from served to preparing"} [409]
```

Captured by `redis-cli` (4 messages, truncated):

```text
orders.new     {"type":"order.created","version":1,"request_id":"d8fcfffb-...","published_at_us":1791574660057268,"order":{"id":1,"public_id":"c414ba9b-...
orders.status  {"type":"order.status_changed","version":1,"request_id":"df05e233-...","published_at_us":1791574660199232,"order":{...
orders.status  {"type":"order.status_changed",...,"published_at_us":1791574660218546,...
orders.status  {"type":"order.status_changed",...,"published_at_us":1791574660240053,...
```

The replay and the rejected requests produced no events: exactly 4 messages for 1 order and 3 transitions.

## Likely interview questions

1. **How do you make `POST /orders` idempotent, including under concurrency?**
   Required `Idempotency-Key`, a UNIQUE index, and `INSERT ... ON CONFLICT DO NOTHING RETURNING id`. The winner continues; the loser (blocked on the index
   until the winner commits) gets no row, loads the existing order and returns it with 200. No read-then-write race. Tested with 10 parallel identical requests: nine 200s, one 201, one row.

2. **What if the same key arrives with a different body?**
   A stored sha256 of the canonical request body is compared; mismatch is 409 so a client bug cannot silently get someone else's order back.

3. **Why compute prices on the server?**
   The client is untrusted. Prices come from the DB at order time and are snapshotted (`unit_price_cents`, `item_name`). Pydantic `extra="forbid"` turns a client-sent price
   into a 422 so integrations fail loudly instead of being ignored.

4. **Why publish after commit?**
   If you publish first and the transaction rolls back, the kitchen sees a phantom order; if the consumer reacts by reading the DB before commit, it reads nothing. Publishing
   after commit means every event refers to durable data. Test: commit patched to raise, nothing published, no row.

5. **What happens if the process crashes between commit and publish?**
   The event is lost; the order is safe. Mitigation today is the REST refetch on reconnect. The principled fix is a transactional outbox (insert an event row in the same
   transaction, relay publishes) with consumers de-duplicating, ideally over Redis Streams. We deferred it because pub/sub is lossy anyway and the window is tiny.

6. **Why a UUID `public_id` for diners?**
   To prevent enumeration of other tables' orders. Sequential ids make `/orders/N` guessable; 122 random bits are not. The internal bigserial remains the PK.

7. **How do you stop two kitchen staff double-advancing an order?**
   Compare-and-set: `UPDATE orders SET status=:new WHERE id=:id AND status=:current RETURNING id`. Zero rows means someone else got there first, so 409. Plus a transition table
   rejecting skips and terminal states.

8. **Why do event failures not fail the request?**
   The order is committed and correct. Returning an error would make the diner retry (safe, but confusing) and conflate "order failed" with "notification failed". We log with
   `request_id` and order id, count a metric (`order_events_publish_failures_total`), and the kitchen self-heals via GET.

9. **How do the allergen filters work, and why SQL?**
   `exclude_allergens` is `NOT (allergens && :list)` and `dietary` is `dietary_tags @> :list` on GIN-indexed `text[]` columns, validated against the closed vocabulary. Exact, deterministic,
   testable (satay disappears when peanut is excluded); never decided by embedding similarity.

10. **What is the main security gap right now?**
    Kitchen endpoints are unauthenticated, and order JSON includes the internal id. Both are documented and acceptable only for a local demo.

## Numbers

| Measurement | Result | Command |
|---|---|---|
| Test suite | 33 passed (8 from Phase 0 plus 25 new), about 3 s, stable over 4 consecutive runs | `cd api && uv run pytest -q` |
| `POST /api/orders` latency, 200 sequential requests, unique keys, 3 lines per order, keep-alive client over localhost to the Docker container | p50 3.7 ms, p95 7.0 ms, p99 27.2 ms, max 39.1 ms | `cd api && uv run python ../bench/order_post_latency.py --token <T4 token>` (saved in `bench/results/phase1-post-orders.txt`) |
| In-handler order creation (price, insert, commit), from Prometheus histogram | mean 1.7 ms (0.346 s / 201 orders) | `curl -s localhost:8000/metrics \| grep order_create_seconds` |
| Redis PUBLISH time, from histogram | mean 0.16 ms (0.032 s / 204 publishes) | `curl -s localhost:8000/metrics \| grep order_event_publish_seconds` |
| Events captured for 1 order + 3 transitions | 4 messages (replay and rejected calls produced none) | `redis-cli SUBSCRIBE orders.new orders.status` |
| Migration head | 0002 | `psql ... select version_num from alembic_version` |

These latencies are single-client, sequential, loopback numbers on one laptop; the p99 and max are single-digit-sample outliers (the p99 is about 2 requests of 200).
They show the order path itself is not the bottleneck for the 100 ms goal; the end-to-end API -> Redis -> Go -> WebSocket measurement is Phase 3.
