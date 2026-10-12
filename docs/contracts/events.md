# Order events contract (v1)

Published by the API (`api/app/events.py`) to Redis pub/sub; consumed by the Go realtime service (Phase 2).

## Rules
- Events are published **strictly after** the DB transaction commits. A subscriber that receives an event can always read the order via REST.
- Delivery is **at-most-once** (Redis pub/sub is fire-and-forget; a publish failure or a subscriber disconnect loses the event). The database is the source of truth: clients recover with `GET /api/kitchen/orders` (kitchen) or `GET /api/orders/{public_id}` (diner) on connect/reconnect.
- Messages are single-line JSON (UTF-8).

## Channels
| Channel | `type` | Published when |
|---|---|---|
| `orders.new` | `order.created` | `POST /api/orders` created a new order (not on idempotent replay) |
| `orders.status` | `order.status_changed` | `PATCH /api/kitchen/orders/{id}/status` succeeded |

## Envelope
```json
{
  "type": "order.created",
  "version": 1,
  "request_id": "c1b6ec2e-e6c1-4e02-9acd-a9588f8509ca",
  "published_at_us": 1791529575820123,
  "order": {
    "id": 42,
    "public_id": "5f0c7a9e-3f5e-4c1e-9b0e-2a7d6c1f8e10",
    "table_label": "T3",
    "status": "received",
    "total_cents": 2850,
    "notes": null,
    "items": [
      {"menu_item_id": 1, "item_name": "Chicken Satay", "qty": 2, "unit_price_cents": 1150, "notes": "no cucumber"},
      {"menu_item_id": 33, "item_name": "Fresh Lemonade", "qty": 1, "unit_price_cents": 500, "notes": null}
    ],
    "created_at": "2026-10-09T07:05:54.783656Z",
    "updated_at": "2026-10-09T07:05:54.783656Z"
  }
}
```

| Field | Type | Notes |
|---|---|---|
| `type` | string | `order.created` or `order.status_changed` |
| `version` | int | Envelope version; currently `1`. Consumers must ignore unknown fields. |
| `request_id` | string or null | The HTTP request id that caused the event. Log it at every hop. |
| `published_at_us` | int | Epoch **microseconds** (`time.time_ns() // 1000`) taken just before PUBLISH. Phase 3 latency bench subtracts this from the WS receive time. |
| `order` | object | Identical shape to the REST `OrderOut` response. |

`order.status` is one of `received, preparing, ready, served, cancelled`. Money is integer cents. Timestamps are ISO-8601 UTC.

## Go consumer notes (Phase 2)
- Route `orders.new` to kitchen clients; route `orders.status` to kitchen clients and to diner clients watching `order.public_id`.
- Never use `order.id` to authorise diners; diners are identified by `public_id`.

# WebSocket API (Go realtime service, port 8080; Vite proxies `/ws` to it)

Implemented in `realtime/internal/hub` and `realtime/internal/server`.

## Endpoints
| Path | Receives |
|---|---|
| `GET /ws/kitchen` | Every `orders.new` and `orders.status` envelope |
| `GET /ws/orders/{public_id}` | Only envelopes whose `order.public_id` equals `{public_id}` (case-insensitive) |

- `{public_id}` must be a UUID; anything else gets **HTTP 400 before the upgrade**.
- **Authorization trade-off:** possession of the order UUID is the capability for `/ws/orders/{public_id}`. There is no login. UUIDv4 (122 random bits) is unguessable, and the order feed contains no payment data, but anyone who obtains the URL can watch that order. `/ws/kitchen` is unauthenticated (staff auth is out of scope, same known gap as the kitchen REST endpoints).
- **Origin check:** the `Origin` header must be same-host or match `ALLOWED_ORIGINS` (comma-separated host patterns; default `localhost:5173,127.0.0.1:5173`). Others get HTTP 403.

## Frames (server to client)
- Text frames. Order events are the Redis envelope bytes **passed through verbatim** (not re-encoded), so they are exactly the envelope above.
- One control frame: `{"type":"resync","version":1}`, sent to all clients after the hub's Redis subscription recovers from a disconnect. Events may have been missed while it was down (pub/sub is at-most-once).
- Clients send nothing. Any data frame from a client closes the connection (policy violation).

## Close codes
| Code | Meaning | Client action |
|---|---|---|
| 1001 going away | Server shutting down or ping timeout | Reconnect with backoff |
| 1008 policy violation, reason `slow consumer` | Client's 64-frame queue overflowed, or it sent data | Reconnect, then resync via REST |

## Keepalive
Server pings every 20 s; a pong not received within 10 s closes the connection. Browsers answer pings automatically.

## Client contract
1. On `open` **and** on every `resync` frame: `GET /api/kitchen/orders` (kitchen) or `GET /api/orders/{public_id}` (diner) to load the snapshot, then apply events.
2. Ignore any event whose `order.updated_at` is older than the state the client already holds for that order (dedupes the gap between snapshot and subscription, and out-of-order delivery).
3. On close/error reconnect with exponential backoff and jitter: 0.5 s, doubling, capped at 10 s; reset after a successful open. After reconnecting, step 1 applies again.
4. The envelope's `published_at_us` is for benchmarking; UIs should not rely on it.
