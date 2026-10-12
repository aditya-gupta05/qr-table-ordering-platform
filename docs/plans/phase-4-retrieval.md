# Phase 4 plan: ingestion + retrieval

Produced by `planner` (Opus, high), 2026-10-10. Scope (PLAN.md Phase 4): docs (FAQ/policy/allergen/PDF menu),
chunking, embeddings, HNSW, FTS + RRF hybrid, cross-encoder rerank, metadata filters, retrieval eval set.
No LLM calls. Everything config-driven so variants are cheap in Phase 7 and the stretch phase.

Environment facts checked: running image has pgvector 0.8.7 (iterative index scans available). fastembed lists
`snowflake/snowflake-arctic-embed-xs` and `all-MiniLM-L6-v2` (both 384-d) and `Xenova/ms-marco-MiniLM-L-6-v2`
(cross-encoder). fastembed's `query_embed` does NOT add the bge query prefix — we add it ourselves.

Concurrency note: Phase 4 code takes an `AsyncConnection`/`AsyncEngine` as a parameter, has its own settings
class (`RETRIEVAL_` env prefix) and its own metrics module. Only edit to shared files: two lines in `main.py` (WP4).

## Decisions
| # | Decision | Choice | Rejected alternative | Why |
|---|---|---|---|---|
| 1 | Corpus | 6 docs, ~12–14k words (~18k bge tokens), in `api/corpus/` | Tiny FAQ only | Below ~5k tokens, 128 vs 512-token chunks barely differ, so the chunking ablation would show nothing |
| 1a | PDF menu | `scripts/gen_menu_pdf.py` (reportlab, `invariant=1`) from `menu.json` plus PDF-only sections | Hand-made PDF; fpdf2 | No drift from menu.json; byte-stable; frames + onPage headers/footers create the pitfalls we want to exercise |
| 1b | Menu enrichment | +5 items (WP1) | Leave 1 peanut item | One peanut item can't distinguish "filter works" from "lucky ranking"; adds lexical traps |
| 2 | Tables | `documents`, `chunk_configs`, `chunks`, `chunk_embeddings`, `menu_item_embeddings`; `menu_items.tsv` generated | One combined table | Menu items have structured filter columns and no chunk config; keeps filters exact |
| 2a | Ablation storage | Several chunk configs × models coexist, keyed by `chunk_config_id` + `model` | Re-ingest per experiment | One eval pass covers all variants; cost (mixed index) measured; `ingest --prune` for production |
| 2b | ANN index | HNSW `vector_cosine_ops`, m=16, ef_construction=64; partial index per model (`WHERE model='<literal>'`); `SET LOCAL hnsw.ef_search=100`, `hnsw.iterative_scan=relaxed_order` + outer re-sort | IVFFlat; single non-partial index | IVFFlat needs training data and is poor at small N; config filter is post-HNSW so recall collapses at ~1/13 selectivity — iterative scan fixes it |
| 2c | Menu-item vectors | No ANN index; exact scan after SQL filters (≈42 rows) | HNSW | Filter-then-rank = exact recall and guaranteed allergen exclusion |
| 3 | Chunkers | `fixed_token` (sliding window + overlap), `recursive_md` (heading → paragraph → sentence → hard cut, + overlap), `sentence` (packing, overlap in whole sentences) | Semantic/embedding chunking | Covers the standard design space; semantic chunking is a stretch item |
| 3a | Token counting | bge WordPiece via `tokenizers` (arctic-xs shares BERT-uncased vocab) | tiktoken | Limits must match the embedder's 512-token truncation |
| 4 | Embedder | fastembed `BAAI/bge-small-en-v1.5`, L2-normalized (cosine = dot), batch 64, query prefix per model in config | Relying on fastembed for prefix | It doesn't add it; prefix on/off is ablated |
| 4a | Second model | `snowflake/snowflake-arctic-embed-xs` | `all-MiniLM-L6-v2` | MiniLM truncates at 256 tokens (confounds chunk-size ablation) and is symmetric-similarity trained |
| 4b | Where embedding runs | Ingestion = CLI (`make ingest`); API embeds queries lazily via `asyncio.to_thread`, warm-up hook, capped ONNX threads | Embed at API startup | Fast startup, event loop stays free |
| 4c | Model cache | Named compose volume `models:/models`, `FASTEMBED_CACHE_PATH=/models` | Bake into image | Avoids concurrent Dockerfile edit; bake later if cold start matters |
| 5 | Search shape | `retrieve()` runs up to 4 ranked lists (menu vector, menu FTS, chunk vector, chunk FTS) by `scope`, then fuses | Single unified index | Rank fusion makes short-row vs long-chunk scores comparable; filters exact per source |
| 5a | Fusion | RRF k=60, candidate_k=50/list; ablate k ∈ {10, 60} | Weighted normalized-score sum | Scales differ (ts_rank vs cosine); tuning weights on ~40 dev queries overfits |
| 5b | FTS | Generated stored `to_tsvector('english', …)` (menu name A, description B), GIN, `ts_rank_cd`; query = OR of terms | `websearch_to_tsquery` default AND | AND returns nothing for natural questions; AND vs OR ablated |
| 5c | Rerank | fastembed `TextCrossEncoder("Xenova/ms-marco-MiniLM-L-6-v2")` on fused top-20 | `BAAI/bge-reranker-base` | 1.04 GB and much slower on CPU; optional measurement |
| 5d | Filters | SQL WHERE on live columns: `available_only` (default true), `exclude_allergens` (`NOT allergens && :x`), `dietary` (`@>`), `categories`, `source_types`; chunks linked to excluded/unavailable items (`chunks.menu_item_ids`) dropped too | Post-filter in Python | A PDF chunk about Chicken Satay must not slip past `exclude_allergens=peanut` |
| 5e | Debug endpoint | `GET /api/search` (read-only, per-stage scores + timings) | CLI only | Phase 5 debugging + HTTP-level latency |
| 6 | Caching | In-process LRU for query embeddings (1024, key = model+prefix+normalized query) + hit/miss counter | Semantic answer cache | Answer cache belongs with the LLM in Phase 5 |
| 7 | Eval labels | Labels = `menu_item_id`s + evidence quotes resolved to char offsets in cleaned text; chunk relevant if it covers ≥50% of a quote | Label chunk ids | Chunk ids change with chunking; offsets survive every config |
| 8 | Config format | TOML (`tomllib`) in `api/app/retrieval/configs/` | YAML | No new dependency |

## Corpus (WP1)
| Doc | source_type | ~Words | Purpose / traps |
|---|---|---|---|
| `faq.md` | faq | 2,500 | ~30 Q&As (hours, reservations, kids, parking, takeaway, Wi-Fi, gift cards); short sections favor small chunks |
| `policies.md` | policy | 2,000 | Large parties, gratuity, corkage, cancellations, accessibility, service animals; long paragraphs favor big chunks |
| `allergen-kitchen-practices.md` | allergen_practices | 2,500 | Shared fryer, sesame oil in wok, GF prep, nut handling, halal sourcing, vegan vs vegetarian, cross-contact disclaimer, "ask staff". Practices only — no allergen matrix |
| `dish-guide.md` | dish_guide | 3,000 | One ~150–250-token section per dish: prep, substitutions. Boundaries make chunk size matter |
| `about-sourcing.md` | about | 1,200 | Suppliers, chef, sustainability — distractor |
| `menu.pdf` | menu_pdf | 2,000 (3–4 pp) | Generated: two-column layout, two-dish-per-row table (row-major extraction interleaves), running header/footer "Page N of M", dot leaders, allergy box drawn in onPage (out of reading order). PDF-only: set lunch, wine pairings, chef's notes |

Cleaning: drop lines repeated on ≥50% of pages, page numbers, normalize dot leaders/whitespace, keep page in chunk
metadata. Evidence: raw vs cleaned "item name and price in the same chunk" rate.

Menu additions: Pad Thai (peanut, egg, fish, shellfish); Satay Tofu Skewers (vegan AND peanut); Pistachio Kulfi
(tree_nut, not peanut); Butternut Squash Soup (vegan, no nuts — lexical trap); Spiced Apple Crumble with nutmeg
(gluten, dairy, no nuts).

## Schema — migration `0003_retrieval.py` (+ models.py)
- `documents(id, slug UNIQUE, source_type TEXT CHECK in (...), title, uri, content TEXT /*cleaned*/, content_hash, metadata JSONB, ingested_at, updated_at)`
- `chunk_configs(id, name UNIQUE, strategy, max_tokens, overlap_tokens, params JSONB, tokenizer, created_at)`
- `chunks(id, document_id FK CASCADE, chunk_config_id FK CASCADE, chunk_index, content, start_char, end_char, token_count, section_path TEXT, page INT NULL, menu_item_ids INT[] DEFAULT '{}', metadata JSONB, tsv TSVECTOR GENERATED STORED, UNIQUE(document_id, chunk_config_id, chunk_index))` + GIN(tsv), GIN(menu_item_ids), btree(chunk_config_id)
- `chunk_embeddings(chunk_id FK CASCADE, model, embedding VECTOR(384), embed_text_hash, PK(chunk_id, model))` + partial HNSW per model string
- `menu_item_embeddings(menu_item_id FK CASCADE, model, content_hash, embedding VECTOR(384), PK(menu_item_id, model))`
- `menu_items.tsv` generated column + GIN

Content hash = sha256(cleaned text + `PIPELINE_VERSION`). Changed → one transaction: delete that doc's chunks for
the config (embeddings cascade), insert new. Unchanged → no-op.

## Retrieval contract (`api/app/retrieval/search.py`)
```
async def retrieve(conn, query: str, filters: Filters, cfg: RetrievalConfig,
                   scope: set[Literal["menu","docs"]] = {"menu","docs"}) -> RetrievalResult
# RetrievalResult.hits: list[Hit(kind: menu_item|chunk, id, menu_item_id|None, document_id|None,
#   chunk_index|None, text, score, ranks: dict[str,int], scores: dict[str,float])]
# RetrievalResult.timings_ms: {embed, vector, fts, fuse, rerank, total}; .config_name
```
`RetrievalConfig`: `mode` (vector|fts|hybrid), `k`, `candidate_k`, `rrf_k`, `rerank`, `rerank_top_n`, `embed_model`,
`query_prefix`, `chunk_config`, `ef_search`, `iterative_scan`, `vector_index` (hnsw|exact), `fts_operator` (or|and),
`prepend_headings`.

## Work packages (all `coder`; no UI this phase)

### WP1 — Corpus, menu enrichment, PDF (~3 h)
- [ ] `api/corpus/*.md` (5 docs) + `scripts/gen_menu_pdf.py` → `api/corpus/menu.pdf` (committed)
- [ ] +5 items in `api/seed/menu.json` (vocab.py terms only)
- Acceptance: `make seed` idempotent at 42 items; PDF byte-identical across runs; docs never contradict menu.json.
- Tests: existing `test_seed_is_idempotent` passes.

### WP2 — Schema + ingestion pipeline (~4 h)
- [ ] `api/alembic/versions/0003_retrieval.py`, `api/app/models.py` (`pgvector.sqlalchemy.Vector(384)`)
- [ ] `api/app/retrieval/{__init__,settings,parse,clean,chunking,embedder,ingest}.py`; CLI
  `python -m app.retrieval.ingest [--config NAME|--all-configs] [--model M] [--menu] [--prune]`, one tx per doc
- [ ] Menu embed template `"{name}. {category}. {description} Dietary: {tags}. Spice: {level}."` (allergens omitted on purpose — safety comes from SQL)
- [ ] Chunk → menu item linking by case-insensitive exact name match (`chunks.menu_item_ids`)
- [ ] `api/app/retrieval/configs/chunking.toml` (grid); Makefile `ingest`; compose models volume/env; AGENTS.md Commands
- Acceptance: re-ingest changes 0 rows; editing one doc re-chunks only it; `alembic downgrade -1` works.
- Tests (`test_chunking.py`, `test_ingest.py`): exact N-token overlap; no chunk > max_tokens (incl. prefix/heading);
  full character coverage; recursive_md never splits mid-heading; sentences kept whole; tiny doc → 1 chunk;
  idempotency; changed doc replaces chunks + cascades embeddings; PDF cleaning strips header/footer (fixture).
  DB tests use a deterministic fake embedder; one real-model smoke test opt-in (`RUN_MODEL_TESTS=1`).

### WP3 — Retrieval core (~4 h)
- [ ] `api/app/retrieval/{config,search,fusion,rerank,cache,metrics}.py`: per-source vector + FTS, filters in WHERE,
  `SET LOCAL hnsw.*`, exact mode, model name inlined as whitelisted literal so the partial index matches; RRF;
  lazy TextCrossEncoder; LRU; `retrieval_stage_seconds{stage,mode}` + `retrieval_query_embed_cache_total{result}`
- Acceptance: ranked hits with provenance + timings for all 3 modes ± rerank; EXPLAIN shows HNSW for default config;
  embedding/rerank in `to_thread`.
- Tests (`test_retrieval.py`): RRF math (ties, single-list items, k effect); **engineered fake vector makes Chicken
  Satay nearest, `exclude_allergens=["peanut"]` returns no peanut item and no chunk linked to one**; vegan filter;
  unavailable excluded by default; scope menu-only; contract shape + descending scores; FTS OR vs AND; cache hit skips embedder.

### WP4 — HTTP endpoint + wiring (~1 h, after the worker/pool tuning lands)
- [ ] `api/app/routers/search.py`: `GET /api/search?q=&mode=&k=&rerank=&exclude_allergens=&dietary=&category=&scope=` (reuse `parse_csv` validation)
- [ ] `main.py`: include_router + optional warm-up (`RETRIEVAL_WARMUP=true`)
- Acceptance/tests: 200 / 422 on unknown allergen; filter pass-through; retrieval histograms on `/metrics`.

### WP5 — Retrieval eval set + harness (~3 h + 15 min user review)
- [ ] `eval/datasets/retrieval_v1.jsonl`, ~66 queries: menu lookup 15, dietary/allergen w/ filters 12, FAQ/policy 15,
  kitchen practices 8, PDF-only 6, comparison/multi-evidence 4, no-answer 6.
  Row: `{id, query, category, split, filters, relevant_menu_item_names, evidence:[{doc_slug, quote, grade:1|2}], notes}`
- [ ] Stratified split 40 dev / 26 test, fixed seed, test ids frozen by hash; tune on dev only, report test once.
- [ ] Leakage controls: queries drafted in diner voice before seeing retrieval output; query↔evidence token Jaccard > 0.5 flagged; user review.
- [ ] `api/app/retrieval/evaluation.py`: recall@{1,3,5,10}, MRR, graded nDCG@k, context precision@k,
  **recall within a 1,024-token context budget**, top-1 score distributions answerable vs no-answer + dev-chosen threshold.
- [ ] `eval/retrieval/run_retrieval_eval.py --configs ... --split dev|test` → `eval/results/phase4/<run_id>/{run.json, per_query.jsonl, summary.json}` + `eval/results/phase4/ablation_summary.md`; latency: 3 warm-up + 5 repeats/query, p50/p95 per stage.
- Acceptance: every quote resolves to offsets (fail loudly otherwise); deterministic metrics across runs.
- Tests: metrics on toy rankings; offset-overlap relevance (quote split across chunks); budget truncation.

### WP6 — Ablations, index study, report (~3 h)
- [ ] Dev-split grid: `fixed_token` {128,256,512} × overlap {0,15%,30%} (9) + `recursive_md` {128,256,512}@15% (3) +
  `sentence` ~256 (1) = 13 configs × {vector, fts, hybrid}. Best 2: ± rerank, ± heading prepend, ± query prefix,
  arctic-xs vs bge, RRF k 10 vs 60, FTS OR vs AND. ± filters on the 12 allergen/dietary queries: unsafe-item rate in top-k (must be 0 with filters).
- [ ] `bench/retrieval_index.py`: HNSW recall@10 vs exact + latency, (a) active config only vs (b) all 13 configs, iterative_scan off vs relaxed_order, ef_search {40,100}.
- [ ] Final configs once on test split; `docs/reports/phase-4-retrieval.md`; PLAN.md Phase 4 → done.
- Expected runtime < 15 min total, $0.
- Acceptance: every report number links to a file in `eval/results/phase4/` or `bench/results/`.

## Report outline (topic → evidence)
- Document ingestion: pipeline, idempotency, PDF pitfalls (raw vs cleaned same-chunk rate)
- Chunk size/overlap: grid table (recall@5, nDCG@5, budget recall, chunk counts)
- Embeddings / cosine: normalization, cosine = dot, bge vs arctic-xs, prefix ablation
- Vector search: HNSW params, post-filter recall pitfall + iterative scan numbers, HNSW vs exact at this N
- Hybrid / reranking: vector vs FTS vs hybrid vs +rerank quality; rerank latency p50/p95
- Metadata filtering: unsafe-item rate off vs on; why filters live in SQL
- Hallucinations: no-answer score distributions → abstention threshold for Phase 5
- Evaluation / retrieval quality: rechunk-proof labels, dev/test split, leakage check
- Latency / caching: per-stage p50/p95, cache hit vs miss
- RAG vs fine-tuning: argued (menu changes daily, provenance, allergen safety); Phase 7 measures RAG vs full-menu-in-context

## Risks
| Risk | Mitigation |
|---|---|
| PDF chunks leak unsafe items past filters | Chunk → item linking; linked chunks filtered; dedicated test |
| Partial HNSW index unused with generic prepared plans (asyncpg) | Inline whitelisted model literal; EXPLAIN test |
| Mixed configs in one HNSW index lose recall | relaxed_order iterative scan + outer re-sort; measured; `--prune` |
| Multiple uvicorn workers each load ~150 MB models / oversubscribe ONNX threads | Lazy load; `RETRIEVAL_ONNX_THREADS`; record RSS |
| Sync ONNX blocks event loop | `asyncio.to_thread`; ruff ASYNC; recheck order-bench latency |
| Silent truncation >512 tokens | Budget = max_tokens − prefix/heading tokens; asserted in tests |
| Model download slow/offline in tests | Fake embedder; real-model test opt-in |
| Eval overfits / leaks doc wording | Frozen test split, Jaccard check, user review, test reported once |
| Docs contradict menu data | Practices-only docs; PDF generated from menu.json; review |
| API image grows (~200 MB onnxruntime) | Record size; accept |

## Effort / order
WP1 3 h · WP2 4 h · WP3 4 h · WP4 1 h · WP5 3 h (+15 min user) · WP6 3 h ≈ 18 h coder time.
Order: WP1 ∥ WP2 → WP3 → WP5 (drafting can start after WP1) → WP4 (after tuning lands) → WP6.

## User decisions (resolved 2026-10-10)
1. New deps APPROVED: `pypdf` (runtime), `reportlab` (dev only), `tokenizers` (explicit).
2. +5 menu items APPROVED (37 → 42).
3. Phase 5 gets a separate `search_info` tool (docs: FAQ/policy/practices/PDF) alongside `search_menu` (menu scope + filters).
4. User reviews drafted eval queries (~15 min) before the test split is frozen — agent stops and asks.
5. Model cache: named compose volume.
