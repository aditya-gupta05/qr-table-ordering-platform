# QR Table-Ordering Platform

A diner scans a table's QR code, browses the menu, asks a RAG chatbot about dishes and allergens, and places an
order that appears on the kitchen screen in real time. FastAPI + Postgres/pgvector serve the ordering API, a Go
WebSocket service fans out orders from Redis pub/sub, and Prometheus + Grafana provide monitoring. See
`docs/PLAN.md` for scope and phase status (currently Phase 0: scaffold).

## Prerequisites
Docker (Compose v2), Go 1.26, [uv](https://docs.astral.sh/uv/), Node 20+ and Python 3.12 (uv fetches it).

## Quickstart
```bash
cp .env.example .env
make up      # build and start postgres, redis, api, realtime, prometheus, grafana
make seed    # idempotent: menu + tables T1..T12
cd web && npm install && npm run dev   # frontend on :5173
```

Compose maps Postgres/Redis to 55432/56379 so they do not collide with locally installed ones.

| Service | URL |
|---|---|
| API | http://localhost:8000 (`/healthz`, `/readyz`, `/metrics`, `/docs`) |
| Realtime | http://localhost:8080 (`/healthz`, `/readyz`, `/metrics`) |
| Prometheus | http://localhost:9090 |
| Grafana | http://localhost:3000 (anonymous viewer; admin creds in `.env`) |
| Web | http://localhost:5173 |

Tests and lint: `make test`, `make lint`. More commands in `AGENTS.md`.
