# QR Table-Ordering Platform

A diner scans a table's QR code, browses the menu, asks a RAG chatbot about dishes and allergens, and places an
order that appears on the kitchen screen in real time. FastAPI + Postgres/pgvector serve the ordering API, a Go
WebSocket service fans out orders from Redis pub/sub, and Prometheus + Grafana provide monitoring. See
`docs/PLAN.md` for scope and phase status (Phases 0-3 done).

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

## Performance
Kitchen order-notification latency = diner taps "Place order" (`POST /api/orders` scheduled) to the kitchen WebSocket
receiving `order.created`, measured on one host clock by `bench/e2e_latency.py`. Full tables, every run and the caveats:
[bench/results/phase3-summary.md](bench/results/phase3-summary.md), [bench/results/phase3-repeats.md](bench/results/phase3-repeats.md),
[docs/reports/phase-3-latency-benchmark.md](docs/reports/phase-3-latency-benchmark.md).

Closed loop, one order at a time (latest of 3 identical runs; ms):

| Scenario | n | p50 | p95 | p99 | max | p99 across the 3 runs |
|---|---|---|---|---|---|---|
| A: sequential, 1 kitchen client | 1000 | 4.94 | 7.28 | 15.18 | 51.7 | 15.2 to 34.4 |
| B: 100 kitchen clients (last client to receive) | 300 | 6.60 | 8.82 | 10.71 | 11.7 | 10.7 to 27.4 |
| D: status change to diner's order socket | 300 | 9.09 | 14.02 | 28.80 | 98.1 | 17.6 to 35.4 |
| A with 1 s idle gap before each order (cold path) | 100 | 27.11 | 44.92 | 57.91 | 73.1 | single run |

Open loop (fixed send timetable, latency measured from the intended send time, 10 kitchen clients, 20 s per rate):

| Target rate | p99 under 100 ms? |
|---|---|
| 100 orders/s, load generator inside the compose network | 3 of 3 runs with 2 workers (p99 23 to 56 ms); 4 of 5 with one worker (9.5 to 217 ms) |
| 200 orders/s, inside the compose network | 3 of 4 runs with 2 workers (p99 23 to 235 ms, median 61); 1 of 5 with one worker (median p99 337 ms). Remaining tail not isolated |
| 50 orders/s, load generator on the macOS host (Docker Desktop port forward) | 2 of 8 runs (p99 41 to 777 ms) |
| 50 / 100 / 200 orders/s from the macOS host, 2 workers (3 runs each) | 2 of 3 / 2 of 3 / 2 of 3 (worst p99 114 / 385 / 834 ms); the 5 to 22 s collapses seen with one worker did not recur |
| 100 orders/s, from the macOS host | 2 of 9 runs; two runs collapsed (p50 over 1 s) |

Plain reading: one order at a time and 100-client fan-out keep p99 under 60 ms in every run (worst p99 across all runs
59.6 ms), but single outliers do cross 100 ms (A max 487 ms in run 1, B with 1 client 257 ms in run 3), so "every
notification under 100 ms" is not a claim this data supports; "p99 under 100 ms" is. The server-side part (API publish to
socket write) stays under 5 ms p99 even at 200 orders/s. Sustained open-loop load above ~50 orders/s does **not** reliably
hold p99 under 100 ms on this laptop through the Docker Desktop port forward, and inside the compose network tails appear at 200 orders/s with the API at only ~50% of one core (see the report for what
was and was not isolated). These are laptop numbers under
background load (load average 6 to 8 on 10 cores), not a Linux server.

Rates above marked "2 workers" are from the follow-up in the report (section "Follow-up: workers and pool sizing", [bench/results/phase3b-repeats.md](bench/results/phase3b-repeats.md)); other rows are the original one-worker runs.

Machine: Apple M2 Pro (10 cores), 16 GiB RAM, macOS (Darwin 25.6.0); Docker Desktop 29.2.0 VM with 10 CPUs and 7.65 GiB RAM;
two uvicorn workers (`API_WORKERS`, was one before the follow-up), one Go hub, Postgres 16 and Redis 7.4 defaults. Dashboard: Grafana "Realtime latency"
(http://localhost:3000, `ops/grafana/dashboards/realtime-latency.json`).

## Retrieval
Ingestion and retrieval for the chatbot (Phase 4): 6 documents (FAQ, policies, kitchen practices, dish guide, sourcing, generated PDF menu) plus the menu table are chunked, embedded
(`bge-small-en-v1.5`, 384-d, cosine) and stored in Postgres with pgvector HNSW and full-text indexes. `retrieve()` fuses vector and full-text lists with RRF; allergen and dietary
filters are SQL, never similarity. Default: `fixed_128_o30` chunks, hybrid search with an AND full-text query, no reranker.

Measured on the frozen 26-query test split (24 answerable), one run, production default:

| recall@5 | success@5 | MRR | nDCG@5 | retrieval latency p50 / p95 |
|---|---|---|---|---|
| 0.832 | 0.958 | 0.812 | 0.733 | 11.7 / 14.3 ms (cache miss, in-process) |

Unsafe items in the top 10 for allergen/dietary queries: 23 to 34% of hits without filters, 0 of 510 with them. The dev-split gain of the chosen default over the first default did not replicate on test
(nDCG@5 -0.015, 95% interval -0.112 to +0.081): the eval set is small (see the report). Cross-encoder reranking added 60 to 800 ms and no measurable quality, so it is off.
Details, every ablation and the commands: [docs/reports/phase-4-retrieval.md](docs/reports/phase-4-retrieval.md), [eval/results/phase4/ablation_summary.md](eval/results/phase4/ablation_summary.md),
[bench/results/phase4-index.md](bench/results/phase4-index.md).
