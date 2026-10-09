# AGENTS.md

Instructions for any coding agent working in this repo. `CLAUDE.md` imports this file; edit rules here, not there.

## Project
QR table-ordering platform. A diner scans a table QR, browses the menu, asks a RAG chatbot questions, and orders.
Orders reach the kitchen screen in real time (<100 ms target). Full scope, decisions, and phase status: `docs/PLAN.md`.

This is an interview-prep portfolio project. Every claim in the README must be backed by a measurement in this repo.

## Architecture
```
web (React)  --HTTP-->  api (FastAPI)  --SQL-->  Postgres + pgvector
     ^                      |
     |                      +--PUBLISH--> Redis pub/sub --SUBSCRIBE--> realtime (Go)
     +-----------------WebSocket-------------------------------------------+
api /chat --> DeepSeek (tool-calling) --tools--> pgvector / FTS / SQL
api, realtime --/metrics--> Prometheus --> Grafana
```

## Layout
| Path | What |
|---|---|
| `api/` | FastAPI app (Python 3.12, uv). Orders, menu, chat, ingestion, analytics |
| `realtime/` | Go WebSocket hub |
| `web/` | React + Vite + TS + Tailwind SPA |
| `bench/` | Latency benchmark |
| `eval/` | Chatbot eval datasets, harness, results |
| `scripts/` | Seed, QR generation, e2e smoke |
| `ops/` | Prometheus and Grafana config |
| `docs/` | `PLAN.md`, `reports/`, `interview-prep.md` |

## Commands
Fill these in as each service is scaffolded; keep them accurate.
- Stack: `cp .env.example .env && make up` (= `docker compose up -d --build`) / `make down` / `make logs`
- Make shortcuts: `make test` (api + realtime) · `make lint` (ruff, go vet, gofmt, web lint) · `make fmt`
- API: `cd api && uv run pytest` (needs compose postgres/redis up; uses DB `qr_test`) · `uv run ruff check . && uv run ruff format --check .` · `uv run alembic upgrade head` (host, against `qr` on :55432) or `make migrate` (in container)
- Realtime: `cd realtime && go test ./... && go vet ./...`
- Web: `cd web && npm run lint && npm run build` · dev server: `npm run dev` (:5173, proxies `/api` and `/ws`)
- Seed: `make seed` (idempotent; or `cd api && uv run python -m seed.seed`)
- QR PNGs: `cd api && uv run python ../scripts/gen_qr.py [--base-url URL]` -> `scripts/qr/`
- Bench (POST /api/orders latency): `cd api && uv run python ../bench/order_post_latency.py --token <qr_token>` (output in `bench/results/`; delete rows with `notes='bench-phase1'` afterwards)
- Event contract: `docs/contracts/events.md`
- Smoke: `TODO` · Eval: `TODO`

## Non-negotiable rules
- **Publish after commit.** Never publish to Redis before the DB transaction commits.
- **Server-side pricing.** Order totals are computed from DB prices; ignore client-sent prices.
- **Idempotent orders.** `POST /orders` requires an `Idempotency-Key`; duplicates return the original order.
- **Allergen safety.** Allergen/dietary answers come from structured columns via SQL filters, never from vector
  similarity alone. If unsure, the bot says so and offers `call_waiter`. Never guess "safe".
- **Grounded answers.** The chatbot answers only from retrieved context or tool results; otherwise it refuses or escalates.
- **Embedding dimension is fixed at 384** in the schema. Changing models means a migration and re-embed.
- **Secrets** live in `.env` (gitignored). Commit `.env.example` only. Never log API keys or full prompts with PII.
- **Structured logs only** (structlog / slog JSON) with `request_id`. No bare `print`.
- **Measure, don't claim.** Latency and quality numbers in docs must come from a script in `bench/` or `eval/` with its output saved.
- Stay in scope (see `docs/PLAN.md`). Propose additions; don't build them unasked.

## Definition of done (per change)
1. Tests pass and lint is clean for every touched service.
2. New behavior has a test (pragmatic: API routes, tool functions, hub fan-out, retrieval functions).
3. Commands section updated if you added or changed one.

## Phase workflow
1. Plan the phase (short checklist in the session, scope from `docs/PLAN.md`).
2. Build it. Verify with tests and by running it.
3. Write `docs/reports/phase-N-<slug>.md` (template below). **Required after every phase. Do not skip.**
4. Update phase status in `docs/PLAN.md`.
5. Stop and let the user review before starting the next phase. Do not commit unless asked.

### Phase report template
```markdown
# Phase N: <name>

## Summary
What was built, in 3-5 lines. Link key files.

## 1. Technologies, languages, libraries, frameworks
For each: what it does here, why it was chosen, the alternative considered and why not.

## 2. Technical challenges and how they were approached
For each: the problem, what was tried, what worked, evidence (test, number, log). Include dead ends.

## 3. Deployment, automation, monitoring, logging
What was added this phase (compose services, scripts, metrics, log fields, dashboards). "None this phase" is fine.

## Likely interview questions
5-10 questions an interviewer could ask about this phase, each with a short answer grounded in this code.

## Numbers
Any measured results, with the command that produced them.
```
Write for the user to study: explain the *why*, use concrete file references, no filler.

## Model and cost policy
Credits are limited and the deadline matters. Use the cheapest model that can do the job; escalate only on evidence.

| Work | Model | Effort | Agent |
|---|---|---|---|
| Planning: phase plans, task breakdown, eval methodology | Opus 5.5 | high | `planner` |
| Architecture: system/schema/API design, trade-off calls | Opus 5.5 | medium | `architect` |
| Python, Go, tests, scripts, phase reports, debugging | Sonnet 5.5 | medium | `coder` |
| Frontend (React components, styling, pages) | Haiku 5.5 | medium | `frontend` |

- Main session runs `opusplan` (Opus in plan mode, Sonnet otherwise). Do implementation outside plan mode.
  Use high effort only while planning; switch back to medium for everything else.
- Delegate to a subagent only when it saves main-context tokens (large, self-contained tasks). Small edits: do inline.
- **Escalate** one tier only after the cheaper model fails the same task twice (tests still red, or wrong design):
  `frontend` → `coder`; `coder` debugging → `architect` (diagnosis only, `coder` applies the fix).
  Note the escalation in the phase report's challenges section.
- `high` effort is for `planner` only; never use `high`/`max` elsewhere without the user's say-so.
- Don't re-read large files or re-run full test suites without reason; run the narrowest test that proves the change.
- DeepSeek API spend (eval runs): estimate call count first and tell the user before any run over ~500 LLM calls.
