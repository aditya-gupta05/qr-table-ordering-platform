# Phase 2: Realtime hub, kitchen board, live order status

## Summary
Orders created through the API now reach the kitchen screen over WebSocket, and a diner's order page updates live when the kitchen
moves it. A Go hub subscribes to Redis pub/sub (the API publishes after commit, Phase 1) and fans events out to browsers.
The React app has real pages: a diner menu and cart, an order status page, and a three-lane kitchen board.
The consistency model is **REST snapshot plus live events**, with a `resync` signal when the hub loses Redis.

Backend (Go):
- Hub: [hub.go](../../realtime/internal/hub/hub.go), [subscriber.go](../../realtime/internal/hub/subscriber.go), [metrics.go](../../realtime/internal/hub/metrics.go), [design doc](../../realtime/internal/hub/doc.go)
- HTTP/WS routes: [server.go](../../realtime/internal/server/server.go); wiring and shutdown: [main.go](../../realtime/cmd/realtime/main.go)
- Tests: [hub_test.go](../../realtime/internal/hub/hub_test.go), [integration_test.go](../../realtime/internal/hub/integration_test.go), [ws_test.go](../../realtime/internal/server/ws_test.go)
- Contract (events plus WebSocket API): [docs/contracts/events.md](../contracts/events.md)

Frontend (React/TS, `web/src`):
- Socket and consistency: [useLiveSocket.ts](../../web/src/hooks/useLiveSocket.ts), [ws.ts](../../web/src/api/ws.ts), [orderStore.ts](../../web/src/lib/orderStore.ts), [idempotency.ts](../../web/src/lib/idempotency.ts)
- Pages: [Kitchen.tsx](../../web/src/pages/Kitchen.tsx), [Diner.tsx](../../web/src/pages/Diner.tsx), [OrderStatus.tsx](../../web/src/pages/OrderStatus.tsx)
- API layer: [client.ts](../../web/src/api/client.ts), [endpoints.ts](../../web/src/api/endpoints.ts), [types.ts](../../web/src/api/types.ts)

Honest scope notes: the chat panel is an inert placeholder (Phase 5). Kitchen and kitchen-WebSocket endpoints are unauthenticated
(known gap). The latency numbers below are preliminary; Phase 3 does the real benchmark.

## 1. Technologies, languages, libraries, frameworks

**Go `sync.RWMutex` plus per-client buffered channels (hub registry).** Fan-out takes the read lock and does only non-blocking
`select`/`default` sends; register/unregister take the write lock and are rare. Alternative: one hub goroutine owning the map, with
register/unregister/broadcast channels (the classic gorilla chat example). That is easy to reason about, but adds a channel hop to the
latency-critical path and serialises everything through one goroutine. Nothing blocks while our lock is held, so the lock is simpler and faster.

**`coder/websocket`.** Used for `Accept` (with `OriginPatterns`), `CloseRead`, `Ping`, context-aware `Write`. Chosen over `gorilla/websocket`
(see Phase 0): context cancellation on reads and writes, safe concurrent `Write` and `Ping`, and `CloseRead`, which handles control frames
and closes the socket if a client sends data, so we need no read loop.

**go-redis `PubSub.Receive`.** Gives raw `*redis.Subscription`, `*redis.Message` values, which lets us detect resubscription. The convenience
`Channel()` API hides that and drops messages when its buffer fills. Alternative: Redis Streams (see consistency story).

**WebSocket, not SSE, not polling.** See the interview questions. Short version: the browser only needs server-to-client pushes, so SSE would
work too; WebSocket was chosen because the project's claim and the Go hub are about WebSocket fan-out, because it supports server pings and close
codes that carry meaning (1008 slow consumer), and because the contract can later grow client messages (e.g. kitchen acks). Polling was rejected: at a
1 s poll the average notification delay is 500 ms, which already breaks the 100 ms target.

**Prometheus histograms with sub-millisecond buckets** (0.1 ms to 250 ms) for `realtime_publish_to_send_seconds`, since the default buckets start at 5 ms and
could not resolve the number we care about.

**Frontend: React 19 + Vite + Tailwind v4 + react-router, no state library.** Local `useState`/`useRef` and two small pure modules
([orderStore.ts](../../web/src/lib/orderStore.ts), [ws.ts](../../web/src/api/ws.ts)) hold all merge logic, so it is testable without React. Alternative:
Redux/TanStack Query. Query would give caching and refetching for free, but our merge rules (version guard, event queueing during snapshot) are the
interesting part, and writing them explicitly is clearer for interview purposes.

**`crypto.randomUUID()` idempotency keys** ([idempotency.ts](../../web/src/lib/idempotency.ts)): one key per cart body, reused on retry and double tap,
re-minted only when the body changes (otherwise the server answers 409).

**Fonts and design** (Fraunces and Atkinson Hyperlegible, high-contrast dark kitchen board with age timers amber at 10 min and red at 20): chosen for
legibility at arm's length in a kitchen and for allergen text readability; allergen badges are always visible ("No allergens listed" when empty).

## 2. Technical challenges and how they were approached

### End-to-end consistency story (the part interviewers probe)

**Why snapshot plus events, not events alone?**
Pub/sub gives no history. A kitchen tablet opened (or reloaded) at 6 pm would see only orders placed after it connected. So every client does:
`GET` the REST snapshot (`/api/kitchen/orders` or `/api/orders/{public_id}`), then applies live events on top. The database is the source of truth; the
socket is only a latency optimisation. The same snapshot call is what repairs every failure mode below.

**The race between snapshot and subscription, and the `updated_at` guard.**
If the client opens the socket first and fetches the snapshot after, an event can arrive before the snapshot response and then be overwritten by an
older snapshot row; if it fetches first, an event can slip between the response and the subscription. The solution is order-independent merging: every
order carries `updated_at`, and a merge keeps the incoming copy only if it is not older than the held one
([`isNewer`](../../web/src/api/ws.ts), [`applyOrderToBoard`](../../web/src/lib/orderStore.ts)). Applying events twice, late or out of order then converges
to the same state. The kitchen also **queues events while a snapshot is loading** ([Kitchen.tsx](../../web/src/pages/Kitchen.tsx), `syncing` and `queued` refs) and applies them
on top of the snapshot, and ignores a stale snapshot response if a newer `sync()` started (`generation` counter). One detail: backend timestamps have microsecond
precision but JS `Date` keeps milliseconds, so [`toMillis`](../../web/src/api/ws.ts) truncates the fraction; two updates inside the same millisecond compare equal and the incoming one wins (`>=`), which is
the safe direction. A strict version counter column would be more precise; `updated_at` was enough and needed no schema change.

**Resync on subscription loss.**
Redis pub/sub is at-most-once: if the hub's Redis connection drops for 1 s, any events published in that second are lost to that hub. go-redis reconnects by itself,
but silently. [subscriber.go](../../realtime/internal/hub/subscriber.go) treats "a `Receive` error, then a subscribe confirmation covering all channels" as recovery
and broadcasts `{"type":"resync","version":1}` to every client, which then refetches its snapshot. A client that is itself disconnected gets the same repair from
its reconnect (`onOpen` runs the snapshot). Tested by killing the subscriber's Redis connection by name (`CLIENT KILL`), and live (numbers below).

**At-most-once, and what we would switch to for at-least-once.**
Today there are two loss windows: the API crashing between COMMIT and PUBLISH (Phase 1), and Redis pub/sub dropping messages when a subscriber is away. Both are healed
by the snapshot, which is acceptable for a kitchen board that self-corrects. If guaranteed delivery mattered, the upgrade is a **transactional outbox**
(event row written in the same transaction as the order, relayed after commit) feeding **Redis Streams with consumer groups**: durable, replayable by id,
acknowledged per consumer, so a restarting hub resumes where it left off. Costs: consumers must de-duplicate on event id (at-least-once means duplicates),
more moving parts, and a polling or blocking-read hop on the latency path. We kept pub/sub because the snapshot already gives eventual correctness and pub/sub is the fastest option.

**Why RWMutex plus per-client buffered channels, and the slow-consumer policy.**
The fan-out loop must never wait on a network socket: one stuck phone on bad Wi-Fi would delay the kitchen's notification. So each client owns a 64-slot queue and a writer goroutine
with a 5 s write timeout; the dispatcher only does non-blocking enqueues. If a queue is full the client is **closed with 1008 "slow consumer"** instead of dropping messages silently or blocking.
Rationale: a client that silently loses events would show wrong state with no indication; a closed client reconnects, refetches the snapshot, and is correct again. Closing is the backpressure signal.
The close handshake can itself block on a dead peer, so it runs in a separate goroutine. Unit test: with a stuck writer and queue size 2, 20 events dispatch without blocking and a fast sibling receives all 20 while the slow one is closed.

**Horizontal scaling.**
Every Go instance runs its own Redis subscription. Redis pub/sub delivers each message to all subscribers, so each instance forwards to the clients connected to it; no instance needs to know about any other
and there is **no sticky-session requirement** and no shared connection registry. The load balancer can send a reconnecting client to any instance. Limits: each instance receives every event (fine until event volume, not connection count, dominates),
and a client moving between instances relies on the snapshot for continuity, which it does anyway. Per-instance memory scales with connections; Go goroutines (about 2 KB stacks) make tens of thousands per box feasible, but we have not measured that.

### Problems hit (problem, cause, fix)

1. **WebSocket upgrade failing behind the logging middleware.** The request-ID middleware wrapped the `ResponseWriter` in a `statusRecorder`, which hid `http.Hijacker`, so `websocket.Accept` could not take over the connection.
   Fix: implement `Hijack` and `Unwrap` on the recorder. Side effect: the `http_request` line for a WebSocket is logged when the socket closes, with status 101 and the whole connection duration.
2. **Shutdown did not close sockets.** `http.Server.Shutdown` ignores hijacked connections. Fix: call `hub.Shutdown()` (close all with 1001 going away, refuse new registrations) before `srv.Shutdown`. Verified: logs show `hub_shutdown clients_closed:1` on `docker compose restart`.
3. **Tests polluting the running stack.** Pub/sub is server-wide, so a test publishing to `orders.new` on the dev Redis would reach the live container. Fix: channels are parameters of `RunSubscriber`; tests use unique per-run names. Also `CLIENT KILL` is by address found via `CLIENT LIST` name, so the resync test only kills its own connection.
4. **No "reconnected" callback in go-redis.** Without handling, a failing `Receive` also spins hot. Fix: infer recovery from the subscription-count confirmation and back off 100 ms doubling to 2 s between failed receives.
5. **Dropping a slow client needs lock juggling.** Removing needs the write lock, but the dispatcher holds the read lock while enqueueing. Fix: collect slow clients during the loop, release the read lock, then close and remove them.
6. **Refactoring `server.New`** into an `Options` struct plus `NewRegistry`, so hub metrics and the Go collectors share one Prometheus registry. Phase 0's handler tests were updated.
7. **Frontend: tsc "no overlap" error** after an early return narrowed a type; the later redundant check was removed.
8. **Frontend: BSD `sed -i` requires an empty suffix argument on macOS** (same family of problem as Phase 0); redone with `sed -i ''`.
9. **Frontend: Place order button stuck disabled** because the blocker checked a stale error set; fixed to check only the current cart lines.
10. **Frontend: a transient 404 in a screenshot** turned out to be a timing artifact, not a bug (order page loaded before the order existed in the test script).
11. **API ergonomics gap:** a 422 `detail` comes in three shapes (string, object, Pydantic array), so [client.ts](../../web/src/api/client.ts) normalises them into `messages`. A uniform error envelope in the API would be cleaner; not changed in this phase. The API also exposes no restaurant name, so `web/src/config.ts` holds a placeholder ("Lantern Kitchen").

Not verified (stated plainly): the kitchen 409 rollback path in a real browser, the browser's full backoff timing during a realtime outage (the Python replica below stands in), and double-tap in the browser (covered by the API replay tests). The web app has no automated tests yet; `npm run lint` reports 2 intentional oxlint warnings (set-state-in-effect after an await).

### Frontend behaviours worth knowing
- **Idempotency:** one key per cart body, held in a ref; a ref guard blocks the double tap before React re-renders. A changed cart gets a new key.
- **Kitchen optimistic moves:** the card moves immediately; on a 409 or error it rolls back unless a newer server version already arrived, shows a notice, and runs `sync()`. Cancel needs two taps.
- **Reconnect:** [`backoffDelay`](../../web/src/hooks/useLiveSocket.ts) = min(10 s, 0.5 s times 2^attempt), then a random point in the upper half (50% to 100%) for jitter; reset on a successful open.

## 3. Deployment, automation, monitoring, logging

- **Compose:** no new services. `realtime` image rebuilt (distroless); `ALLOWED_ORIGINS` is configurable (default `localhost:5173,127.0.0.1:5173`). Vite proxies `/ws` to the hub, so the browser uses one origin.
- **New realtime metrics** (on `:8080/metrics`): `realtime_connections{topic_kind}`, `realtime_events_received_total{channel}`, `realtime_messages_sent_total`, `realtime_slow_consumer_drops_total`, `realtime_resyncs_total`, `realtime_fanout_seconds`, `realtime_publish_to_send_seconds` (the Phase 3 number: now minus the API's `published_at_us` at the moment the frame is written; valid because API and hub share a host clock locally).
- **Logs (slog JSON):** `ws_connect` / `ws_disconnect` (topic, remote), `fanout` (request_id, type, channel, recipients, dropped), `redis_subscription_lost`, `redis_subscribed`, `resync_broadcast`, `ws_slow_consumer_dropped`, `hub_shutdown`. The `request_id` set by the diner's HTTP request now appears in the API log, the event envelope, and the hub's `fanout` line, completing the trace API to Redis to hub.
- **Makefile / docs:** `make test` runs `go test -race`; AGENTS.md Commands updated. Contract extended with the WebSocket API (endpoints, close codes, keepalive, client rules).
- **Not done:** Grafana panels for the realtime metrics (Phase 3), auth on WebSocket endpoints, load test of many connections, TLS/`wss` termination.

### Live verification (compose stack)
Python client on `/ws/kitchen` plus an order topic and a random-UUID topic:
```text
created 201 207 4fec7855-...
patch preparing / ready / served -> 200 each
kitchen frames:   order.created(received), status_changed(preparing), (ready), (served)
my-order frames:  status_changed(preparing), (ready), (served)
other-order frames: []        # random UUID received nothing
```
After the run `realtime_connections` returned to 0 for both kinds (disconnect accounting works). The browser run by the frontend agent saw a live kitchen highlight for an externally created order and the order page flipping preparing to ready, with captured frames matching the contract.

## Likely interview questions

1. **WebSocket vs SSE vs polling for this feature?**
   Polling costs latency (average half the interval) and load; at 1 s intervals it already violates a 100 ms goal. SSE is one-way over HTTP with built-in reconnect and is a perfectly good fit for pure push, but offers no
   client-to-server channel and text only. WebSocket gives full duplex, binary, server pings and meaningful close codes. We only push today, so SSE would also work; WebSocket was chosen for the project's stated architecture and
   to leave room for client messages. SSE's HTTP/1.1 six-connection-per-origin limit is a consideration the other two avoid.

2. **How do you handle backpressure?**
   Per-client bounded queue (64) plus a writer goroutine; the fan-out loop uses non-blocking sends. A full queue means the client is too slow, so it is closed with 1008 and recovers via reconnect plus snapshot.
   We deliberately do not buffer unboundedly (memory growth, stale data) and do not block the loop (head-of-line blocking for everyone).

3. **What is a reconnect storm and how do you mitigate it?**
   When the hub restarts or the network blips, every client reconnects at the same instant, then every client also refetches its snapshot, hammering the API and DB. Mitigation: exponential backoff (0.5 s to 10 s) with jitter
   so retries spread out, a cap, and resetting backoff only after a successful open. The snapshot endpoint is a single indexed query. For large fleets add server-side connection rate limiting and have the server send a randomised reconnect delay hint.

4. **Why not rely on events alone?**
   Pub/sub has no history and is at-most-once. Late joiners and anything missed during a disconnect need the snapshot; the `updated_at` guard makes the merge order-independent.

5. **How do you scale the hub horizontally?**
   Run N instances, each subscribed to Redis; pub/sub delivers every event to every instance and each forwards to its own sockets. No sticky sessions, no inter-instance coordination. The bottleneck becomes event volume per instance
   or Redis itself; beyond that, shard channels by table or restaurant.

6. **What happens if Redis restarts?**
   The hub's `Receive` fails, it backs off and resubscribes, then broadcasts `resync`; clients refetch the snapshot. Measured live: subscription lost, re-established after about 1.2 s, resync delivered to the client about 0.9 s after the restart command returned.

7. **Why close slow clients instead of dropping messages?**
   Silent drops leave the UI wrong with no signal. A close is explicit: the client reconnects and rebuilds state from the snapshot, so consistency is restored. The metric `realtime_slow_consumer_drops_total` makes it visible.

8. **How do you keep a diner from watching another table's order?**
   The topic is the order's `public_id` (UUIDv4); possession is the capability. Non-UUID ids are rejected with 400 before the upgrade. Trade-off: anyone with the URL can watch; no login. The kitchen socket is unauthenticated, a known gap.

9. **Why RWMutex and not a single hub goroutine with channels?**
   Fan-out only reads the registry and never blocks while holding the lock, so a read lock is cheap and avoids serialising all dispatches and adding a channel hop. Register/unregister are rare writes.

10. **How would you guarantee at-least-once delivery?**
    Transactional outbox in the order transaction, relayed to Redis Streams, consumer groups with acknowledgement and replay from the last id; consumers de-duplicate by event id. Costs more latency and complexity; justified only if missing an event cannot be repaired by a snapshot.

11. **How do you detect dead connections?**
    Server pings every 20 s with a 10 s deadline; failure closes the socket and unregisters the client, so half-open TCP connections do not leak. Browsers answer pings automatically.

## Numbers

| Measurement | Result | How |
|---|---|---|
| Go tests | 15 tests (8 server, 7 hub incl. 2 Redis integration), pass with `-race -count=3`, no skips | `cd realtime && go test -race -count=3 -v ./...` |
| API tests | 33 pass (unchanged) | `cd api && uv run pytest -q` |
| `realtime_publish_to_send_seconds`, 57 frames over loopback, 1 kitchen client | p50 between 0.1 and 0.25 ms; p95 between 0.5 and 1 ms; all under 2.5 ms; mean 0.316 ms (0.018 s / 57) | `curl -s localhost:8080/metrics \| grep publish_to_send` after 51 orders and 3 transitions |
| `realtime_fanout_seconds` | mean about 0.03 ms (0.00165 s / 54 dispatches) | same scrape |
| Live fan-out correctness | 4 kitchen frames, 3 order-topic frames, 0 frames for an unrelated UUID | Python client script against the live stack |
| Connections gauge after disconnect | 0 kitchen, 0 order | `/metrics` |
| `docker compose restart realtime`, kitchen client with replicated backoff (0.5 s base, jitter) | graceful 1001 close received, hub down about 0.28 s (log: shutdown 20:06:26.018 to startup .298), client reopened at t=1.60 s after being closed at t=1.22 s, i.e. **about 0.4 s to a working socket** (first backoff drew 0.37 s); `docker compose restart` command itself took 0.52 s | `restart.py` scratch client, timestamps as above |
| `docker compose restart redis`, client stayed connected | hub logged subscription lost at 20:06:34.54, re-subscribed at 20:06:35.71 (**1.17 s**), `resync` frame received by the client at t=10.91 s, **about 0.9 s after the restart command returned (10.04 s)**; `realtime_resyncs_total` incremented | same script; `docker compose logs realtime` |
| Reconnect on realtime restart did not deliver a `resync` | expected; the browser's `onOpen` runs the snapshot instead | same script |

Caveats: the restart timings are single runs on one laptop with the Python client standing in for the browser's backoff logic (the frontend's own timing during an outage was not exercised). The latency histogram is 57
samples with one local client; it measures only Redis receive to socket write and needs the Phase 3 benchmark (many clients, end-to-end from the order POST) before it can be quoted in the README.
