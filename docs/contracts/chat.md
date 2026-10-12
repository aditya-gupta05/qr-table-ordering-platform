# Chat API contract (v1)

Authoritative contract for the diner chatbot, waiter requests and the `staff.requests` event. Frontend code only against this file. All paths are under `/api`. JSON is UTF-8; timestamps are ISO 8601 UTC (`2026-10-11T18:02:11.123456Z`). Fields marked `null` are always present.

## Conventions
- Errors use FastAPI's `{"detail": ...}`: a string for simple errors, an object for the ones listed below, or the standard 422 list.
- The session id (UUID) is a capability: whoever holds it can read and write that session. The client stores it in `sessionStorage["chat:" + qr_token]`.
- Message text is **plain text**. Never render it as HTML or Markdown.
- Max message length 500 characters (server returns it as `max_message_chars`).
- Prices are integer cents.

## Vocabularies
- `allergen`: `gluten dairy egg peanut tree_nut soy fish shellfish sesame mustard celery sulphites`
- `dietary_tag`: `vegetarian vegan gluten_free halal dairy_free`
- Message `status` (assistant): `answered | not_found | clarify | off_topic | needs_staff | blocked | error`
- Turn `outcome`: `answered | not_found | escalation_offered | off_topic | clarify | action_only | blocked | error`
- Action `status`: `proposed | accepted | dismissed`
- Waiter `reason`: `allergy_or_dietary | question_not_answered | diner_asked_for_staff | other`
- Waiter `status`: `open | acknowledged | closed`; `source`: `chat | button`

## Schemas

### Message
```json
{
  "id": 118,
  "role": "assistant",
  "text": "The Pad Thai ($14.50) contains peanut, egg and soy. Please tell your server about any allergy.",
  "status": "answered",
  "citations": [
    {"kind": "menu_item", "menu_item_id": 12, "name": "Pad Thai", "price_cents": 1450},
    {"kind": "passage", "chunk_id": 904, "title": "Policies", "section": "Gratuity", "source_type": "policy"}
  ],
  "allergen_notice": {
    "items": [
      {"menu_item_id": 12, "name": "Pad Thai", "allergens": ["egg", "peanut", "soy"], "dietary_tags": []}
    ],
    "disclaimer": "Allergen information comes from our menu data and may not cover cross-contact in the kitchen. Please confirm with your server before ordering.",
    "source_chunk_id": 311
  },
  "actions": [],
  "feedback": null,
  "created_at": "2026-10-11T18:02:11.123456Z"
}
```
| Field | Type | Notes |
|---|---|---|
| `id` | int | Stable message id; used for feedback. |
| `role` | `"user"` \| `"assistant"` | User messages have `status: null`, `citations: []`, `allergen_notice: null`, `actions: []`. |
| `text` | string | Plain text. User text is shown as stored (PII-redacted: emails, phone numbers, card numbers replaced by `[email]`, `[phone]`, `[card]`). |
| `status` | string \| null | Assistant status (see vocabularies). |
| `citations` | array | `menu_item`: `{kind, menu_item_id, name, price_cents}`; `passage`: `{kind, chunk_id, title, section, source_type}`. UI: item chips scroll to `#item-{menu_item_id}`; passages render as "Source: {title} › {section}". |
| `allergen_notice` | object \| null | Present on every allergen/dietary-sensitive turn, built by the server from menu data independent of the model text. `items` may be empty (then show only `disclaimer` and the call-waiter button). `source_chunk_id` may be `null`. Render in a bordered box as "Contains: ..." with the disclaimer. Never style as safe/green. |
| `actions` | array of Action | Proposals attached to this message. |
| `feedback` | `true` \| `false` \| `null` | `true` = helpful. |
| `created_at` | string | |

### Action
`add_to_cart`:
```json
{
  "type": "add_to_cart", "action_id": "5b6f0c8e-0d0e-4c3a-9d55-1f0a7c1b2e44",
  "menu_item_id": 12, "name": "Pad Thai", "price_cents": 1450, "qty": 2, "note": "extra lime",
  "allergens": ["egg", "peanut", "soy"], "allergen_conflict": ["peanut"], "status": "proposed"
}
```
`allergen_conflict` lists allergens the diner declared in this session that the item contains (empty array when none; show a warning when non-empty). `note` may be `null`.

`call_waiter`:
```json
{
  "type": "call_waiter", "action_id": "9a1d2c3b-aaaa-4b1c-8d2e-0f1e2d3c4b5a",
  "reason": "allergy_or_dietary", "note": "Severe peanut allergy", "status": "proposed"
}
```
Neither action does anything until the diner decides. For `add_to_cart` the client adds to its own cart on accept, then posts the decision. Accepting `call_waiter` is what creates the waiter request (server side).

### WaiterRequest
```json
{
  "id": 7, "table_label": "T3", "reason": "allergy_or_dietary", "note": "Severe peanut allergy",
  "source": "chat", "status": "open", "session_id": "0c0e7a5e-5a0b-4f58-a8f9-6e7d6c1f8e10",
  "created_at": "2026-10-11T18:03:00.000000Z", "updated_at": "2026-10-11T18:03:00.000000Z"
}
```
`note` and `session_id` may be `null` (`session_id` is null for button requests without a chat session).

## Endpoints

### POST /api/chat/sessions
Request: `{"table_token": "<qr_token>"}`

201:
```json
{"session_id": "0c0e7a5e-5a0b-4f58-a8f9-6e7d6c1f8e10", "table_label": "T3", "max_message_chars": 500, "turns_remaining": 20}
```
Errors: 404 `{"detail": "Unknown table"}`; 422 validation.

### GET /api/chat/sessions/{sid}
200: `{"session_id", "table_label", "turns_remaining", "messages": [Message, ...]}` ordered oldest first (history restore on reload; includes actions with their current status).
Errors: 404 `{"detail": "Unknown session"}`.

### POST /api/chat/sessions/{sid}/messages
Request: `{"text": "Is the pad thai spicy?", "client_message_id": "c-8f2b..."}`
- `text`: 1 to 500 characters after trimming. `client_message_id`: client-generated unique string (1 to 64 chars). Resending the same `client_message_id` (for example a retry after a network drop) replays the stored reply with no new LLM call and does not consume a turn.
- Pre-stream errors (plain JSON, before any SSE frame):
  - 404 `{"detail": "Unknown session"}`
  - 409 `{"detail": "Turn in progress"}`: one turn in flight per session; wait for it to finish.
  - 422 validation (empty/too long text).
  - 429 `{"detail": {"message": "...", "reason": "session_limit" | "table_rate" | "budget"}}`: show the message and offer the Call a waiter button.

**SSE response** (request with `Accept: text/event-stream`, the default for the UI). Response headers: `Content-Type: text/event-stream`, `Cache-Control: no-cache`, `X-Accel-Buffering: no`. The server sends a `: ping` comment line every 10 s while working; ignore comment lines. Frames are `event: <name>\ndata: <single-line JSON>\n\n`, in this order:

1. `turn` (exactly one)
2. `status` (zero or more)
3. exactly one of `answer` or `error`
4. `done` (exactly one, always last)

```
event: turn
data: {"turn_id": "6f1c1d0e-6a7b-4a6b-8c1d-2e3f4a5b6c7d", "turn_index": 3, "request_id": "c1b6ec2e-e6c1-4e02-9acd-a9588f8509ca"}

event: status
data: {"stage": "searching_menu", "label": "Searching the menu..."}

event: status
data: {"stage": "checking_answer", "label": "Checking the answer..."}

event: answer
data: {"message": { ...Message (role "assistant")... }}

event: done
data: {"turn_id": "6f1c1d0e-6a7b-4a6b-8c1d-2e3f4a5b6c7d", "outcome": "answered", "latency_ms": 2140}
```
- `status.stage`: `thinking | searching_menu | searching_info | checking_item | checking_answer`; `label` is display text.
- The `answer` frame carries the complete, validated message. There is no token streaming; do not expect partial text.
- On failure the server sends `error` instead of `answer`:
```
event: error
data: {"code": "llm_unavailable", "message": "Sorry, I can't answer right now.", "fallback": { ...Message with status "error" and a call_waiter action... }}
```
  `code`: `llm_unavailable | timeout | budget | internal`. `fallback` is a normal Message (apology plus a `call_waiter` action) that the UI should display like an answer. `done.outcome` is `error`.
- The user's own message is persisted before the stream starts; the UI shows it optimistically and does not need an echo frame. After the turn, `GET /sessions/{sid}` returns both messages.
- `done.outcome` values are listed in Vocabularies. `blocked` means a safety check replaced the model text with a structured fallback; display `answer` normally.

**JSON response** (request with `Accept: application/json`): 200
```json
{"turn_id": "6f1c1d0e-6a7b-4a6b-8c1d-2e3f4a5b6c7d", "outcome": "answered", "message": { ...Message... }}
```
When the server runs with `CHAT_DEBUG=true` an extra `"debug"` object (tool calls, token usage; no secrets) is included. On `error` outcomes the JSON status is still 200 and `message` is the fallback Message.

### POST /api/chat/sessions/{sid}/actions/{action_id}
Request: `{"decision": "accepted" | "dismissed"}`

200: `{"action": Action, "waiter_request": WaiterRequest | null}`
- `waiter_request` is non-null only when accepting a `call_waiter` action (created, or the table's already-open request).
- Idempotent: repeating the same decision returns the same result. Changing a settled decision returns 409 `{"detail": "Action already resolved"}`.
- Errors: 404 `{"detail": "Unknown action"}` (also wrong session).
- For `add_to_cart` the server only records the decision (`accepted` also marks the session as converted); the client applies the cart change itself.

### POST /api/chat/messages/{message_id}/feedback
Request: `{"helpful": true}` (or `false`). 204, no body. 404 `{"detail": "Unknown message"}`. Only assistant messages accept feedback (else 422). Repeating overwrites.

### POST /api/waiter-requests
Request: `{"table_token": "<qr_token>", "reason": "diner_asked_for_staff", "note": "optional, max 200 chars", "session_id": "<uuid>|omitted"}` (`source` is set to `button` by the server).
- 201 `WaiterRequest` when created; 200 with the existing `WaiterRequest` when the table already has an open one (at most one open request per table).
- Errors: 404 `{"detail": "Unknown table"}`; 422 validation.

### GET /api/waiter-requests?status=open
200: `{"items": [WaiterRequest, ...]}` oldest first. `status` is one of the waiter statuses; default `open`. Used by the kitchen screen for its snapshot on load and WebSocket reconnect.

### PATCH /api/waiter-requests/{id}
Request: `{"status": "acknowledged" | "closed"}`. 200 `WaiterRequest`. 404 `{"detail": "Unknown waiter request"}`. 409 `{"detail": "Invalid transition"}` (allowed: open to acknowledged, open or acknowledged to closed). Unauthenticated, same known gap as the kitchen endpoints.

## Event: staff.requests
Redis channel `staff.requests`, forwarded by the Go realtime service to `/ws/kitchen` clients only (never to diner sockets). Published strictly after the DB commit; at-most-once, so clients re-fetch `GET /api/waiter-requests?status=open` on connect and reconnect.

| `type` | Published when |
|---|---|
| `waiter.requested` | a new waiter request was created (not when an existing open one is returned) |
| `waiter.updated` | `PATCH /api/waiter-requests/{id}` changed the status |

```json
{
  "type": "waiter.requested",
  "version": 1,
  "request_id": "c1b6ec2e-e6c1-4e02-9acd-a9588f8509ca",
  "published_at_us": 1791529575820123,
  "waiter_request": {
    "id": 7, "table_label": "T3", "reason": "allergy_or_dietary", "note": "Severe peanut allergy",
    "source": "chat", "status": "open", "session_id": "0c0e7a5e-5a0b-4f58-a8f9-6e7d6c1f8e10",
    "created_at": "2026-10-11T18:03:00.000000Z", "updated_at": "2026-10-11T18:03:00.000000Z"
  }
}
```
The frame is forwarded to kitchen WebSocket clients unchanged (single-line JSON text frame). The kitchen strip should upsert by `waiter_request.id` and hide entries whose status is `closed`.

## Behaviour notes (as implemented in WP3)
- Content negotiation: SSE only when `Accept` contains `text/event-stream`; any other `Accept` (including none or `*/*`) gets the JSON response.
- Retrying after an `error` outcome: resend the SAME `client_message_id`. The server re-runs the turn (no extra turn is consumed) instead of replaying the error. A successful turn replays its stored reply with zero LLM calls (SSE replay sends `turn`, `answer`, `done` only, no `status` frames).
- A `429` or `409` never consumes a turn. A client that disconnects mid-turn cancels it server side; the user message stays stored and the same `client_message_id` can be resent.
- `GET /sessions/{sid}` may end with a user message that has no assistant reply (turn in flight or cancelled).
- Every allergen/diet-sensitive turn carries `allergen_notice`, including `blocked` and `error` turns (then `items` may be empty).
- `blocked` and `error` messages always include a `call_waiter` action.
- `error.fallback` is a persisted Message with a real `id` (feedback works on it); only if even persisting fails is it a synthetic message with `id: 0`.
- Declared allergens are detected from the diner text and only ever grow within a session; they are applied as SQL exclusions in later searches.

## Error codes summary
| Status | Where | Meaning |
|---|---|---|
| 404 | all | unknown table / session / action / message / waiter request |
| 409 | messages | a turn is already in progress for the session |
| 409 | actions, PATCH | already resolved / invalid transition |
| 422 | all | validation (empty or over-long text, bad enum, wrong role for feedback) |
| 429 | messages | `reason` = `session_limit` (20 turns per session), `table_rate` (60 turns per table per hour), `budget` (daily LLM budget used up) |
| SSE `error` | messages | `llm_unavailable`, `timeout`, `budget`, `internal`; always carries a `fallback` Message |
