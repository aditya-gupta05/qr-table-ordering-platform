# Build Plan

Source of truth for scope and phase order. Update the status column as phases finish.

## Goal
Implement exactly what these resume bullets claim, with measured evidence, plus a decent frontend:

- Kitchen order-notification latency under 100 ms: Go WebSocket service ← Redis pub/sub ← FastAPI.
- FastAPI RAG chatbot: pgvector semantic search + LLM tool-calling over menu data, integrated into ordering;
  "reduces diner questions to waitstaff" (measured via a deflection-rate proxy, stated honestly).

## Decisions (2026-10-08)
| Area | Choice |
|---|---|
| Frontend | React + Vite + TypeScript + Tailwind (one SPA: diner, order status, kitchen) |
| API | FastAPI, SQLAlchemy 2.0 async, Alembic, Pydantic v2, uv |
| Realtime | Go, `coder/websocket`, `go-redis` |
| Data | Postgres 16 + pgvector (HNSW), Redis 7 |
| LLM | DeepSeek (OpenAI-compatible API, tool-calling) |
| Embeddings | Local fastembed, `BAAI/bge-small-en-v1.5` (384-d) |
| Tenancy | Single restaurant (no `restaurant_id`) |
| Payments | None |
| Run | Local `docker compose up` only |
| Logging | structlog (Python), slog (Go), JSON, request-ID propagated API → Redis → Go → WS |
| Monitoring | Prometheus + Grafana in compose |
| Testing | pytest, `go test`, one e2e smoke script |
| Eval judge | LLM-as-judge + hand-labeled set, judge agreement reported |

Out of scope: admin CRUD, staff auth, payments, cloud deploy, CI, OpenTelemetry, multi-tenant.

## Phases
| # | Phase | Status |
|---|---|---|
| 0 | Scaffold: repo layout, compose (pg+pgvector, redis, prometheus, grafana), schema/migrations, seed, logging setup | done |
| 1 | Ordering API: menu, tables, orders, server-side pricing, idempotency, publish-after-commit | done |
| 2 | Go realtime hub + kitchen board + diner live order status | done |
| 3 | Latency benchmark (p50/p95/p99 end-to-end) + Grafana dashboard | done |
| 4 | Ingestion + retrieval: docs (FAQ/policy/allergen/PDF menu), chunking, embeddings, HNSW, FTS + RRF hybrid, cross-encoder rerank, metadata filters, retrieval eval set | done ([report](reports/phase-4-retrieval.md)) |
| 5 | Chatbot: DeepSeek tool loop (`search_menu`, `search_info`, `get_item`, `add_to_cart`, `call_waiter`), guardrails, caching, chat UI, analytics | todo |
| 6 | Frontend polish + e2e smoke test | todo |
| 7 | Chatbot evaluation + `chatbot-eval.md` + `interview-prep.md` | todo |

## Phase 7 evaluation spec
- ~150 hand-labeled questions: menu lookup, dietary/allergen, FAQ/policy, paraphrases, tool-call orders,
  should-refuse (off-menu, unanswerable), prompt injection.
- Retrieval: recall@k, MRR, nDCG@k, context precision.
- Generation: faithfulness / hallucination rate (LLM judge + human spot-check), answer correctness,
  allergen-safety accuracy, tool-call accuracy, correct-refusal rate.
- System: per-stage latency p50/p95 (embed, retrieve, rerank, LLM), tokens and cost per query, cache hit rates.
- Ablations: chunk size/overlap, vector vs hybrid, ± rerank, ± metadata filters, two embedding models,
  RAG vs full-menu-in-context.
- Interview topics to cover with evidence: RAG vs fine-tuning, chunk size, chunk overlap, embeddings, vector search,
  cosine similarity, reranking, hallucinations, evaluation, retrieval quality, latency, caching, document ingestion,
  metadata filtering.

## Stretch (after Phase 7)
Keep improving retrieval/chatbot. Keep the retrieval pipeline config-driven so variants are cheap to add and re-evaluate.
