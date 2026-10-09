# Phase 0: Scaffold

## Summary
Phase 0 builds the skeleton every later phase hangs on: a six-service Docker Compose stack, a FastAPI service with the full
Phase 1 schema already migrated, a Go service with health/metrics endpoints, a placeholder React SPA, and the logging and
metrics plumbing. Nothing business-level exists yet (no order endpoints, no WebSocket logic, no chat).

- Compose: [docker-compose.yml](../../docker-compose.yml), [Makefile](../../Makefile), [.env.example](../../.env.example)
- API: [api/app/](../../api/app/), [migration](../../api/alembic/versions/0001_initial.py), [seed](../../api/seed/seed.py), [menu.json](../../api/seed/menu.json), [tests](../../api/tests/)
- Realtime: [server.go](../../realtime/internal/server/server.go), [main.go](../../realtime/cmd/realtime/main.go), [hub design stub](../../realtime/internal/hub/doc.go)
- Ops: [prometheus.yml](../../ops/prometheus/prometheus.yml), [dashboard](../../ops/grafana/dashboards/overview.json), [provisioning](../../ops/grafana/provisioning/)
- Web: [web/](../../web/) (three placeholder routes, Tailwind v4, dev proxy)

Honest note: Phase 0 had no major dead ends. Almost everything passed on the first run; the problems in section 2 are small
but each teaches something. No model escalation was needed.

## 1. Technologies, languages, libraries, frameworks

### API stack (Python 3.12)
**FastAPI.** HTTP framework with async handlers, dependency injection, and OpenAPI for free. Chosen because the chatbot
(Phase 5) streams and awaits several I/O calls (DB, embeddings, LLM), which suits async. Alternative: Flask/Django. Flask is
sync-first; Django brings an ORM and admin we explicitly don't want (admin CRUD is out of scope).

**SQLAlchemy 2.x async + asyncpg.** Typed `Mapped[...]` models in [models.py](../../api/app/models.py), async engine in
[db.py](../../api/app/db.py). asyncpg is the fastest Postgres driver for asyncio and SQLAlchemy supports it natively.
Alternative: psycopg 3 (also async). It is a fine choice, but asyncpg is the more common pairing and has a binary protocol;
we are not using anything psycopg-specific. Alternative to the ORM: raw SQL. We keep the ORM for models/migrations and can
still drop to SQL for the allergen filters.

**Alembic.** Versioned schema migrations; [env.py](../../api/alembic/env.py) runs async. Chosen over `create_all()` because
the embedding dimension is fixed in schema (AGENTS.md rule) and changes must be migrations, and because the migration is
itself tested.

**pydantic-settings.** [config.py](../../api/app/config.py) reads env vars into a typed object. Fails loudly on bad types,
no hand-rolled `os.environ` parsing. Alternative: python-dotenv plus dicts, which has no validation.

**uv.** Dependency manager and lockfile (`uv.lock`), also used in the Dockerfile with `--frozen`. Alternative: pip + requirements.txt
(no lock resolution) or Poetry (slower, heavier). uv is fast and the lock makes Docker builds reproducible.

**structlog.** JSON logs with contextvars, so `request_id` is bound once in middleware and appears on every log line,
including ones emitted deep in code. Alternative: stdlib `logging` with a JSON formatter. That works but has no clean
per-request context binding. We still route stdlib/uvicorn logs through structlog's `ProcessorFormatter` so everything
is one JSON stream ([logging.py](../../api/app/logging.py)).

**prometheus-client.** Counter and Histogram defined in [middleware.py](../../api/app/middleware.py), served by `/metrics`.
Alternative: `prometheus-fastapi-instrumentator`. It would be less code, but writing ~15 lines ourselves makes the
cardinality decisions explicit and teachable.

**pytest + pytest-asyncio + httpx.** `httpx.ASGITransport` calls the app in-process, no server needed. Session-scoped loop
so the asyncpg pool survives across tests.

**ruff.** Lint plus format in one tool (rules E, F, I, B, UP, SIM, ASYNC; line length 100). Alternative: black + flake8 + isort, three tools.

**qrcode[pil], pgvector (Python package).** QR PNGs for [scripts/gen_qr.py](../../scripts/gen_qr.py); `pgvector` installed
now so Phase 4 needs no dependency change.

### Realtime (Go)
**Go + net/http + log/slog.** The WebSocket fan-out (Phase 2) is many long-lived connections with a broadcast loop; goroutines
make that cheap (a few KB each) and there is no GIL. Alternative: do WebSockets in FastAPI. That is simpler (one service) but
a Python process fans out on one event loop, and the resume claim is specifically a Go WS service. slog is stdlib structured logging
(no dependency), JSON handler to stdout.

**prometheus/client_golang.** Default Go collectors give `go_goroutines` and process metrics for free; we add
`realtime_build_info`. Uses a dedicated registry so we control exactly what is exposed.

**go-redis v9.** Standard Redis client, used now for `PING` in `/readyz` and for subscribe in Phase 2. Wrapped behind a
tiny `Pinger` interface so handler tests need no Redis.

**coder/websocket (pinned, unused).** Blank-imported in [hub/doc.go](../../realtime/internal/hub/doc.go) so `go mod tidy`
keeps it. Chosen for Phase 2 over gorilla/websocket: it is context-aware (`Read(ctx)`/`Write(ctx)`), has a smaller
API, supports concurrent writers safely, and is actively maintained (gorilla was archived for a while and its concurrency
rules are easy to get wrong).

**distroless/static:nonroot.** Final image has just the static binary: 20.6 MB, no shell, no package manager, non-root.
Alternative: alpine (has a shell, easier debugging, larger attack surface). Cost: no `curl` for healthchecks (see section 2).

### Data and infrastructure
**Postgres 16 via `pgvector/pgvector:pg16`.** One database for relational data, full-text search (Phase 4) and vectors.
Alternative: a dedicated vector DB (Pinecone, Qdrant). Not chosen because: (a) menu items and their embeddings can be
updated in one transaction, no sync problem between two stores; (b) allergen/dietary filtering is plain SQL next to the
vector search, which matters for safety; (c) our corpus is hundreds of chunks, far below where a specialised engine pays off;
(d) one fewer service to run. The `vector` extension is enabled in migration 0001 so Phase 4 only adds a table.

**Redis 7 (`redis:7.4-alpine`).** Pub/sub bus API -> Go hub. Alternative: Postgres LISTEN/NOTIFY (no extra service, but
payload limits and ties the hub to the DB) or Kafka/NATS (overkill). Redis pub/sub is fire-and-forget, which is acceptable
because the DB is the source of truth and clients refetch on reconnect.

**Prometheus (pull) + Grafana.** Prometheus scrapes `/metrics` every 5 s. Pull over push because targets are known and
static, a dead target shows up as `up == 0` for free, and services stay unaware of the monitoring backend. Push
(Pushgateway) is for short-lived batch jobs. Grafana is provisioned from files so the dashboard is code.

**Docker Compose.** Single-command local run, which is the stated deployment target. Alternative: Kubernetes, out of scope.

**Makefile.** Thin wrapper (`up`, `down`, `logs`, `migrate`, `seed`, `test`, `lint`, `fmt`) so nobody memorises flags.

### Frontend
**Vite + React + TypeScript + Tailwind v4 (`@tailwindcss/vite`) + react-router.** Standard SPA stack; Tailwind v4 needs only
`@import "tailwindcss"` and the Vite plugin, no config file. Routes `/t/:token`, `/order/:id`, `/kitchen` are placeholder headings.
The dev server proxies `/api` -> :8000 and `/ws` -> :8080 ([vite.config.ts](../../web/vite.config.ts)) so the browser
sees one origin and we avoid CORS.

**oxlint.** The current Vite template ships oxlint instead of ESLint; I kept the default. It is a Rust linter and much faster.
Trade-off: smaller plugin ecosystem than ESLint.

## 2. Technical challenges and how they were approached

Format: problem, root cause, fix, lesson.

1. **Python 3.13 venv instead of 3.12.** `uv init` picked the newest interpreter on the machine. Root cause: no pin.
   Fix: `uv python pin 3.12` (writes `api/.python-version`), recreated `.venv`. Lesson: dev and container interpreters must match;
   pin explicitly.

2. **Alembic deprecation warning** about `prepend_sys_path` splitting on spaces/colons. Root cause: missing `path_separator`
   in `alembic.ini`. Fix: `path_separator = os`. Lesson: read warnings from tools at version 1.x of a new major.

3. **Ruff `ASYNC240`: blocking `Path.read_text()` in an async function** ([seed.py](../../api/seed/seed.py)). Root cause: file I/O
   blocks the event loop. Fix: `asyncio.to_thread`. Lesson: negligible for a seed script, but the lint rule prevents the habit
   spreading to request handlers.

4. **BSD sed on macOS.** `sed -i` with `\n` in the replacement failed (`invalid command code`). Root cause: macOS sed is not GNU sed.
   Fix: do multi-line edits in Python or the edit tool. Lesson: avoid sed for anything beyond one-line substitutions.

5. **`gen_qr.py` could not import `app`.** Error: `ModuleNotFoundError: No module named 'app'`. Root cause: Python puts the
   *script's* directory on `sys.path`, not the working directory, and the script lives in `scripts/`. Fix: insert `api/` into
   `sys.path` at the top of the script (with `noqa: E402` on the imports). Lesson: `sys.path[0]` is the script dir.

6. **Alembic cannot run inside a running event loop.** `env.py` uses `asyncio.run`, which raises if called from pytest's
   loop. Fix: the test helper calls `command.upgrade` via `asyncio.to_thread` (a fresh thread gets a fresh loop). `env.py`
   also honours `ALEMBIC_DATABASE_URL` so tests can migrate `qr_test` without changing app settings.

7. **Keeping tests off dev data.** Root cause of the risk: the engine is created at import time from env. Fix: `conftest.py`
   sets `DATABASE_URL` to `qr_test` *before* importing `app`; a session fixture drops and recreates `qr_test` (`DROP DATABASE ... WITH (FORCE)`,
   via the admin `postgres` DB) and migrates it; the empty-DB migration test uses a separate throwaway `qr_test_migration`.
   Redis uses DB 15. Evidence: dev DB `qr` still held 6/37/12 rows after repeated test runs. `ops/postgres/init.sql` also creates
   `qr_test` on a fresh volume, but the fixture does not rely on it.

8. **Middleware design: pure ASGI, not `BaseHTTPMiddleware`.** `BaseHTTPMiddleware` runs the app in a separate task, which
   historically breaks contextvar propagation and buffers streaming responses (the chat endpoint will stream). Pure ASGI avoids both.
   Subtlety: the route template (`/items/{id}`) only exists in `scope["route"]` *after* routing, so metrics are recorded in a
   `finally` after the app returns. Unmatched paths are labelled `unmatched`, and `/metrics` is not self-counted.
   Evidence: [test_health.py](../../api/tests/test_health.py) asserts a 404 path never appears in the metrics output.

9. **Duplicate and context-less access logs.** Uvicorn's access logger prints its own line without `request_id`. Fix: disable
   `uvicorn.access`, route `uvicorn`/`uvicorn.error` through the root JSON handler, and emit our own `http_request` line from
   the middleware. Result: one line per request, always with `request_id`.

10. **`go mod tidy` removed `coder/websocket`.** Root cause: tidy drops unused modules. Fix: blank import in `internal/hub/doc.go`
    so the version stays pinned. Alternative would have been to add it in Phase 2, which is also reasonable; I followed the spec.

11. **Healthcheck on a distroless image.** No shell, curl or wget, so `curl /healthz` is impossible. Fix: the binary has a
    `-healthcheck` flag that GETs `127.0.0.1:$PORT/healthz` and exits 0/1; compose runs `["CMD", "/realtime", "-healthcheck"]`.
    Lesson: security-minimal images move debugging tools into the app; alpine would have avoided this at the cost of a larger image.

12. **`curl` globbing the PromQL query.** `rate(http_requests_total[1m])` returned nothing because curl treats `[1m]` as a range
    glob. Fix: `curl -g`. Cost five minutes of confusion about whether Prometheus was scraping.

13. **Idempotent seed needs a conflict target.** `INSERT ... ON CONFLICT` needs a unique constraint to target, and items only
    had a serial PK. Fix: add `UNIQUE(category_id, name)` (`uq_menu_items_category_name`) to model and migration. Rules: the seed
    never overwrites `is_available` (runtime state, not seed data), and tables are inserted only when the label is missing, so
    `qr_token` is generated once and never rotates (QR codes already printed would break). Evidence: `test_seed_is_idempotent`
    runs the seed twice and compares counts and every token.

14. **Web lint is oxlint, not ESLint.** The spec said ESLint; the current `create vite` template generates oxlint. Kept the
    template default and recorded it as a deviation rather than fighting the scaffold.

Also worth knowing: the Postgres/Redis on the host (Homebrew) occupy 5432/6379, so compose maps 55432 and 56379 (see below).

### Design decisions worth defending
- **Host port remap.** Defaults collide with Homebrew services; binding the same port would either fail or, worse, tests would
  silently hit the host DB. Distinct ports make that impossible. In-network, services still use 5432/6379.
- **`text[]` + GIN for `allergens` and `dietary_tags`.** The safety rule says allergen answers come from SQL filters, never from
  vector similarity. `allergens @> ARRAY['peanut']` is exact and GIN-indexed; an embedding of "contains peanuts" could
  rank an unsafe dish highly. Vocabularies live in one module, [vocab.py](../../api/app/vocab.py), and the seed validates against it so a typo
  like `tree-nut` fails loudly. Alternative: a join table (more normalised, more joins). Arrays suit a closed 12-item vocabulary.
- **Price snapshot in `order_items`.** `unit_price_cents` and `item_name` are copied at order time so editing or removing a
  menu item never rewrites history or old totals. Money is integer cents, never floats.
- **`orders.idempotency_key UNIQUE NOT NULL`.** Prepared now; Phase 1 will rely on the unique constraint (insert, catch conflict,
  return the original) so concurrent duplicates are resolved by the database, not by a racy read-then-write.
- **`order_status` as a Postgres enum.** The database rejects invalid states; the order of labels documents the lifecycle.
  Trade-off: adding a value later needs a migration (acceptable, rare). `ix_orders_status_created_at` serves the kitchen query
  "open orders, oldest first".
- **`vector` extension now.** Extension creation needs superuser-ish rights and is easy to forget; doing it in 0001 means Phase 4 is
  only a table and an HNSW index.
- **`qr_token` = `secrets.token_urlsafe(16)`.** 16 random bytes = 128 bits, 22 URL-safe characters. Sequential table ids in
  the QR would let anyone walk `/t/1`, `/t/2`... and place orders against other tables (enumeration). Random tokens make the URL a
  capability. Tokens are stored as-is (not hashed) since they are identifiers, not user passwords; they can be rotated by a future admin tool.
- **Metric labels use the route template.** Label values multiply into time series. `/orders/{id}` is one series; raw `/orders/123`,
  `/orders/124`... would create one per order and exhaust Prometheus memory, and let a scanner inflate cardinality with garbage
  paths. 404s collapse into `unmatched`.
- **Migrations in the API entrypoint** (`alembic upgrade head && exec uvicorn ...`). Simple and correct for single-instance local
  compose. In production with N replicas starting together they would race on the schema (Alembic takes no cross-process lock
  on its own, though Postgres DDL serialises) and a bad migration would crash-loop every replica. There you run a one-off
  migration job/init container before rolling out. `exec` makes uvicorn PID 1 so it receives SIGTERM.
- **Request IDs.** Accept an incoming `X-Request-ID` (so a gateway or the benchmark can supply one) or generate a uuid4, echo it
  in the response, and put it in every log line. Both services use the same header and UUID-shaped ids.

## 3. Deployment, automation, monitoring, logging

**Compose services** ([docker-compose.yml](../../docker-compose.yml)); all images pinned, no `latest`:

| Service | Image | Host port | Health |
|---|---|---|---|
| postgres | `pgvector/pgvector:pg16` | 55432 | `pg_isready` |
| redis | `redis:7.4-alpine` | 56379 | `redis-cli ping` |
| api | built from `api/Dockerfile` | 8000 | python `urlopen /healthz` |
| realtime | built from `realtime/Dockerfile` | 8080 | `/realtime -healthcheck` |
| prometheus | `prom/prometheus:v3.5.0` | 9090 | none |
| grafana | `grafana/grafana:12.1.0` | 3000 | none |

`api` has `depends_on: condition: service_healthy` for postgres and redis, so migrations never start against a database that is
still initialising. `realtime` waits for redis. Prometheus/Grafana use plain `depends_on` (start order only); they tolerate
targets appearing late. Volumes: `pgdata` and `grafanadata`.

**Dockerfiles.** API: `python:3.12-slim`, uv copied from its pinned image, dependency layer keyed on `pyproject.toml` + `uv.lock`
so code edits don't reinstall packages, runs as non-root uid 10001, venv in `/opt/venv`. Realtime: multi-stage Go build with
`CGO_ENABLED=0`, final distroless nonroot.

**Makefile.** `make up` (build+start), `down`, `logs`, `migrate`, `seed` (runs inside the api container via `docker compose exec`),
`test` (pytest + go test), `lint` (ruff check/format, go vet, gofmt, oxlint), `fmt`.

**Logging.** JSON to stdout from both services. Example real line from the API:

```json
{"method": "GET", "path": "/readyz", "status": 200, "duration_ms": 63.41, "event": "http_request", "request_id": "c1b6ec2e-e6c1-4e02-9acd-a9588f8509ca", "level": "info", "logger": "http", "timestamp": "2026-10-09T07:05:54.783656Z"}
```

Go emits the same fields via slog (`msg=http_request`, `request_id`, `method`, `path`, `status`, `duration_ms`). Request-ID propagation
today is per-service; in Phase 2 the API will put the `request_id` into the Redis message, the Go hub will log it on receive
and on each WebSocket write, so one order can be traced API -> Redis -> hub -> client with a single grep. No API keys or
prompts are logged (rule in AGENTS.md).

**Monitoring.** [prometheus.yml](../../ops/prometheus/prometheus.yml) scrapes `api:8000/metrics` and `realtime:8080/metrics` every 5 s;
`/api/v1/targets` shows both `up`. API series: `http_requests_total{method,route,status}` and
`http_request_duration_seconds` (buckets 5 ms to 5 s). Realtime: Go runtime/process collectors plus `realtime_build_info{version}`.
Grafana is provisioned entirely from files: datasource ([prometheus.yml](../../ops/grafana/provisioning/datasources/prometheus.yml)),
dashboard provider, and one dashboard (API request rate and p95 latency per route; realtime goroutines and uptime). p95 uses
`histogram_quantile(0.95, sum by (le, route) (rate(..._bucket[1m])))`. Anonymous viewer access is on for local convenience.
Phase 3 will add latency panels for the notification path.

**Readiness vs liveness.** `/healthz` only says the process is up (used for container health). `/readyz` checks dependencies
(`SELECT 1`, Redis `PING`, returns 503 if either fails) so an orchestrator would stop routing traffic without restarting a healthy process.

**Not done, by choice.** No CI, no cloud deploy, no tracing/OpenTelemetry, no auth (all out of scope in `docs/PLAN.md`).

## Likely interview questions

1. **How do you avoid high-cardinality metrics?**
   Label by route *template* taken from `scope["route"]`, never the raw path; unmatched requests get `route="unmatched"`.
   Test: a 404 path must not appear in `/metrics` ([test_health.py](../../api/tests/test_health.py)). Never label by user, order id or request id.

2. **Why request IDs, and how do they work here?**
   They let you stitch one request's logs together across services. Middleware reads or generates `X-Request-ID`, binds it into
   structlog contextvars (so every log line has it, even deep in call stacks), and echoes it in the response. Go mirrors this with
   slog. Phase 2 passes it through Redis to the hub.

3. **How do you keep tests from touching dev data?**
   Separate database `qr_test` set via env before the app imports; the fixture drops/recreates only that database; migration test uses
   its own throwaway DB; Redis DB 15. Compose ports are non-default, so tests can never hit the host's Postgres by accident.

4. **Why Postgres + pgvector instead of a vector database?**
   Transactional consistency between menu rows and embeddings, SQL filters (allergens, availability) in the same query as
   similarity search, one less service, and the corpus is tiny. I'd reconsider at tens of millions of vectors or with heavy
   ANN-specific features.

5. **Why run migrations at startup, and when wouldn't you?**
   It makes `docker compose up` self-contained and is safe with one replica. With several replicas or zero-downtime deploys
   you run migrations once as a separate job, and keep migrations backward compatible with the previous app version.

6. **Why store allergens as arrays with GIN instead of embedding them?**
   Safety requires exact, auditable answers. `allergens @> ARRAY[...]` / `NOT (allergens && ...)` is deterministic and
   index-backed; similarity search can miss or mis-rank. Embeddings are only for fuzzy questions.

7. **Why Go for the realtime service?**
   Cheap goroutines per connection, no GIL, small static binary (20.6 MB image). A Python hub could work at this scale, but the
   project's claim is a Go WS service with a sub-100 ms target, and Phase 3 will measure it rather than assume.

8. **Liveness vs readiness?**
   Liveness: process is alive; restart if it fails. Readiness: dependencies reachable; stop sending traffic if it fails. Restarting
   the API because Redis is down would not help, so `/healthz` doesn't check dependencies.

9. **Why pure ASGI middleware?**
   `BaseHTTPMiddleware` wraps the app in a separate task: contextvar propagation is fragile and streaming is buffered. Pure ASGI is a
   few more lines but keeps both properties, which the streaming chat endpoint needs.

10. **How is the seed idempotent, and why do QR tokens never regenerate?**
    Upserts keyed by unique constraints (`menu_categories.name`, `(category_id, name)`); tables inserted only if the label is
    missing. Regenerating a token would invalidate printed QR codes. Test runs the seed twice and compares every token.

11. **Why snapshot price and name in `order_items`?**
    Menu edits must not change historical orders or totals. Server-side pricing uses the DB price at order time and stores it.

12. **Why Prometheus pull instead of push?**
    Static targets, free liveness signal (`up`), services stay decoupled from the monitoring system. Push is for short-lived jobs.

## Numbers

| Measurement | Result | Command |
|---|---|---|
| Seed row counts (after 2 runs) | 6 categories, 37 items, 12 tables | `make seed` twice; `docker compose exec -T postgres psql -U qr -d qr -c "select ..."` |
| API tests | 8 passed in 1.15 s | `cd api && uv run pytest -q` |
| Go tests | `internal/server` ok (4 test functions plus metrics test) | `cd realtime && go test ./...` |
| Image sizes | realtime 20.6 MB; api 525 MB; postgres (pgvector) 650 MB; redis 58.7 MB; prometheus 423 MB; grafana 905 MB | `docker images` |
| `/readyz` latency, API (20 requests, localhost) | p50 3.9 ms, max 15.4 ms | `for i in $(seq 20); do curl -s -o /dev/null -w "%{time_total}\n" localhost:8000/readyz; done \| sort -n` |
| `/readyz` latency, realtime (20 requests) | p50 1.1 ms, max 1.8 ms | same, port 8080 |
| Prometheus targets | api up, realtime up | `curl -s localhost:9090/api/v1/targets` |
| Grafana | health ok, version 12.1.0, dashboard "QR Ordering Overview" provisioned | `curl -s localhost:3000/api/health` |
| Web build | passes (js 259 kB, 82 kB gzip) | `cd web && npm run build` |

The readyz latencies are a small local sample on one machine (p50 is the 10th of 20 sorted values) and include a real Postgres query
and Redis ping for the API; they are not a benchmark. The latency benchmark that matters (order -> kitchen) is Phase 3.
