# Phase 4: Ingestion and retrieval (chunking, embeddings, HNSW, hybrid, rerank, filters, eval)

## Summary
Phase 4 builds everything the chatbot will read from and measures it, with no LLM calls ($0). Six documents (five markdown files plus a generated
4-page PDF menu, 11,207 words of markdown and 2,765 bge tokens of cleaned PDF) are parsed, cleaned, chunked 15 ways, embedded with two
models and stored in Postgres with pgvector HNSW and full-text indexes. `retrieve()` ([search.py](../../api/app/retrieval/search.py)) runs up to four ranked
lists (menu vector, menu FTS, chunk vector, chunk FTS), fuses them with RRF, optionally reranks with a cross-encoder, and applies allergen and dietary
filters in SQL. A 65-query labelled set ([retrieval_v1.jsonl](../../eval/datasets/retrieval_v1.jsonl), 39 dev / 26 test, test split frozen by hash) and a
harness ([run_retrieval_eval.py](../../eval/retrieval/run_retrieval_eval.py)) turn every design choice into a number.

Headline, honestly: **production default is `fixed_128_o30` + hybrid with an AND full-text query, no rerank. On the frozen test split it scores
nDCG@5 0.733, recall@5 0.832, MRR 0.812 at p50 11.7 ms / p95 14.3 ms per retrieval. The dev-split gain that selected it (+0.075 nDCG@5 over the old
default) did NOT replicate on test (-0.015, 95% interval -0.112 to +0.081): with 35 answerable dev queries most of the ablation gaps are noise.**
What did replicate or is large enough not to need statistics: filters take unsafe items in the top 10 from 23-34% of hits to exactly 0; cleaning the PDF
raises "dish fully inside one chunk" from 85.7% to 100%; the iterative-scan setting is the difference between 27% and 99% HNSW recall on a mixed index;
the cross-encoder costs 200-800 ms for no measurable gain; cosine similarity is not a safe "I don't know" signal.

Key files: [api/app/retrieval/](../../api/app/retrieval/) (clean, chunking, embedder, ingest, search, fusion, rerank, cache, evaluation),
[chunking.toml](../../api/app/retrieval/configs/chunking.toml), [corpus](../../api/corpus/), [gen_menu_pdf.py](../../scripts/gen_menu_pdf.py),
[eval/retrieval/](../../eval/retrieval/), [bench/retrieval_index.py](../../bench/retrieval_index.py), [REVIEW.md](../../eval/datasets/REVIEW.md).
Result files: [ablation_summary.md](../../eval/results/phase4/ablation_summary.md) (all tables, bootstrap intervals), [all_runs.md](../../eval/results/phase4/all_runs.md),
[filter_study.md](../../eval/results/phase4/filter_study.md), [pdf_cleaning.md](../../eval/results/phase4/pdf_cleaning.md), [phase4-index.md](../../bench/results/phase4-index.md).

## 1. Technologies, languages, libraries, frameworks

**pypdf + a generated PDF (reportlab, dev only).** The menu PDF is produced by [gen_menu_pdf.py](../../scripts/gen_menu_pdf.py) from `menu.json`, so prices never drift, and it
deliberately contains the pitfalls real PDFs have: two-column layout, running header/footer, dot leaders, hyphenated wraps, a boxed notice drawn out of
reading order. pypdf extracts text with no system dependency. Alternative: PyMuPDF (faster, AGPL, binary wheels) or a hand-made PDF (not reproducible).

**Chunking in `tokenizers` token units** ([chunking.py](../../api/app/retrieval/chunking.py)). Three strategies: `fixed_token` (sliding window with exact N-token overlap), `recursive_md`
(heading, then paragraph, then sentence, then hard cut, with overlap) and `sentence` (whole-sentence packing). Sizes are counted with the bge WordPiece tokenizer
because the embedder truncates at 512 of *those* tokens (arctic-xs shares the vocabulary). Alternative: tiktoken or characters, which would silently disagree with the
model's limit. Semantic (embedding-based) chunking is a stretch item.

**fastembed with `bge-small-en-v1.5` (quantized ONNX, 66 MB, 384-d).** CPU-only, no torch in the API image, ~7 ms per short query on this laptop. Vectors are L2-normalised
in [embedder.py](../../api/app/retrieval/embedder.py), so cosine similarity equals the dot product and pgvector's cosine distance `<=>` is `1 - cos`. The dimension is fixed at 384 in the schema (AGENTS.md rule).
fastembed does not add bge's query instruction, so the code adds `"Represent this sentence for searching relevant passages: "` itself and the prefix is an ablated switch.
Second model: `snowflake-arctic-embed-xs` (384-d, 90 MB, not quantized). Rejected: all-MiniLM-L6-v2 (truncates at 256 tokens, which would confound the chunk-size study) and any hosted embedding API (cost, latency, PII).

**Postgres + pgvector 0.8 HNSW (m=16, ef_construction=64, `vector_cosine_ops`), one partial index per model.** A partial index (`WHERE model = '...'`) lets two models share one table; the model name
is inlined as a whitelisted literal so the planner can match the index predicate. Alternative: IVFFlat (needs training data, poor at 100-1,000 rows), a separate vector database (another service for ~1,600 vectors).

**Postgres full-text search** (generated `tsvector` column, GIN, `ts_rank_cd`, `to_tsquery('english', ...)`). Lexical match for exact terms ("T3", "LanternGuest", "corkage") with no new service. Query terms are
OR-ed or AND-ed by [`build_tsquery`](../../api/app/retrieval/search.py) from `[a-z0-9]` tokens only (no tsquery-syntax injection). Alternative: BM25 in a separate engine (Elasticsearch/Tantivy); not worth it at this size.

**Reciprocal Rank Fusion** ([fusion.py](../../api/app/retrieval/fusion.py)). Alternative: weighted sum of normalised scores, which needs tuning weights on ~39 queries and compares incomparable scales (cosine vs `ts_rank_cd`, menu rows vs chunks).

**Cross-encoder `Xenova/ms-marco-MiniLM-L-6-v2`** (fastembed `TextCrossEncoder`, 91 MB ONNX), lazy-loaded and run in a thread. Alternative: `bge-reranker-base` (1 GB, far slower on CPU).

**Evaluation code in pure Python** ([evaluation.py](../../api/app/retrieval/evaluation.py), no DB, 15 unit tests) and a paired bootstrap in [phase4_tables.py](../../eval/retrieval/phase4_tables.py). Alternative: ragas/trec_eval: more dependencies, and the group/offset labelling here is custom.

## 2. Technical challenges and how they were approached

### 2.0 Ablation scoreboard (dev, paired bootstrap, 35 answerable queries)
Every row is one switch flipped against its baseline, with the 95% interval of the nDCG@5 difference (`*` = interval excludes 0). The two chunk configs are A = fixed_128_o30 and B = recursive_256_o15; full tables incl. recall are in
[ablation_summary.md](../../eval/results/phase4/ablation_summary.md) section 2.

| switch (baseline = hybrid, bge, prefix on, FTS OR, RRF k=60, no rerank) | A: dnDCG@5 [95% CI] | B: dnDCG@5 [95% CI] | verdict |
|---|---|---|---|
| FTS query AND instead of OR | +0.050 [-0.024, +0.141] | +0.021 [-0.044, +0.093] | adopted (point estimate; not significant) |
| RRF k = 10 (vs 60) | +0.001 [-0.004, +0.006] | +0.004 [0.000, +0.011] | irrelevant, keep 60 |
| no bge query prefix | +0.014 [-0.013, +0.045] | -0.007 [-0.026, +0.008] | wash, keep prefix |
| arctic-xs instead of bge | -0.006 [-0.071, +0.074] (R@5 -0.079 *) | -0.032 [-0.079, +0.023] | keep bge |
| headings prepended to the embedded text | **-0.061 [-0.113, -0.010] \*** | +0.001 [-0.032, +0.037] | rejected |
| rerank top 10 | +0.028 [-0.046, +0.113] | -0.003 [-0.072, +0.069] | rejected (cost) |
| rerank top 20 | -0.020 [-0.107, +0.069] | -0.012 [-0.087, +0.060] | rejected (cost) |

Heading prepend hurts the small config because the heading path eats part of a 128-token budget (114 vs 126 content tokens per chunk, 218 vs 190 chunks) and repeats the same words in every chunk of a section, which pulls sibling chunks together; it is neutral at 256 tokens.
Only one of the 14 nDCG intervals above excludes zero (headings at 128 tokens; the one other significant entry in the full tables is arctic's recall gap); the honest summary is that this dev set can reject bad ideas (AND-only FTS, headings at 128) but cannot rank the good ones.

### 2.1 Document ingestion
Pipeline ([ingest.py](../../api/app/retrieval/ingest.py)): parse and clean each file, hash `sha256(cleaned text + PIPELINE_VERSION)`, and per document in one transaction delete+insert its chunks (embeddings cascade) only if the hash or the chunk-config
params changed. Chunks carry `start_char/end_char` into the cleaned text, a section path, a page number and `menu_item_ids` (case-insensitive name matches, deliberately over-linking). Idempotency evidence: a re-run over all 15 configs and both models
inserted 0 documents and 0 chunks (the only writes were 327 embeddings for configs/models that did not exist yet); [test_ingest.py](../../api/tests/test_ingest.py) asserts re-ingest changes nothing and that editing one document re-chunks only that document.

**PDF pitfalls.** `pdf_cleaning.py` ([result](../../eval/results/phase4/pdf_cleaning.md)) measures the effect on the real PDF (42 dishes):

| text | chars | bge tokens | prices kept (of 48) |
|---|---|---|---|
| raw extraction | 12,945 | 3,927 | 48 |
| cleaned (shipped) | 10,356 | 2,765 | 48 |
| first cleaner (the bug) | 10,488 | 2,797 | **6** |

Cleaning removes 30% of the tokens (headers, footers, 42 dot-leader lines of up to 28 tokens each). The share of dishes whose name, price and first six description words are in **one** chunk:
fixed_128_o30 85.7% raw vs **100%** cleaned; recursive_128_o15 78.6% vs 100%; fixed_128_o0 73.8% vs 83.3%. Dead end worth knowing: the first cleaner dropped every line repeated on at least half the pages
after digit-normalising, and `"$11.50"` normalises to `"$#.#"`, so 42 of 48 prices vanished while the tests of that time still passed (they only checked headers and dot leaders). Fix: only the top/bottom 3 lines of a page can be a header/footer and price lines are exempt
([clean.py](../../api/app/retrieval/clean.py)); regression test `test_pdf_cleaning_strips_header_footer_and_dot_leaders_but_keeps_prices`. Not fixed, only measured: the allergy box is drawn first and so is extracted first.

**Stranded headings.** The first chunker could end a chunk on `## Pad Thai` and start the next on the body, so the heading and its text were embedded apart. `glue_headings` now attaches heading-only pieces to the text after them
and `PIPELINE_VERSION` was bumped to "2" so every stored document re-chunks (that is what the version in the hash is for).

### 2.2 Chunk size and overlap (grid on dev, bge, no rerank)
13 configs x 3 modes in [ablation_summary.md section 1](../../eval/results/phase4/ablation_summary.md); selected rows (hybrid unless noted; R@5 = label-group recall, B = recall inside a 1,024-token context):

| config | chunks | tok/chunk | vec nDCG@5 | hyb nDCG@5 | hyb B | dropped by one allergen filter | collateral label loss |
|---|---|---|---|---|---|---|---|
| fixed_128_o0 | 135 | 125 | 0.572 | 0.562 | 0.877 | 15.3% | 18.7% |
| fixed_128_o30 | 190 | 126 | **0.685** | 0.643 | **0.884** | 15.3% | 12.5% |
| fixed_256_o30 | 95 | 250 | 0.576 | 0.612 | 0.791 | 24.6% | 27.7% |
| fixed_512_o0 | 36 | 470 | 0.492 | 0.597 | 0.741 | 31.7% | 39.0% |
| fixed_512_o30 | 48 | 487 | 0.631 | 0.632 | 0.763 | 33.2% | 35.3% |
| recursive_256_o15 (old default) | 103 | 200 | 0.642 | 0.618 | 0.848 | 19.6% | 25.7% |
| recursive_512_o15 | 50 | 406 | 0.608 | 0.599 | 0.756 | 29.0% | 35.3% |

What the grid says: small chunks win on the budget metric (a 1,024-token prompt fits eight 128-token chunks but only two 512-token ones), and overlap helps small windows (fixed_128: nDCG@5 0.572 / 0.622 / 0.685 for 0% / 15% / 30% overlap,
vector mode) because an answer cut at a boundary is whole in the next window. The **budget-recall correction**: plain recall@k favours big chunks (a 512-token chunk covers more quotes per slot); recall within a fixed token budget removes that advantage, and
512-token configs fall from comparable R@10 (0.88-0.93) to 0.72-0.76 B-recall. Chunk size also decides how much allergen filtering over-drops (section 2.9). Caveat: the whole spread between the top configs is inside the bootstrap noise (2b table).

### 2.3 Embeddings and cosine similarity
Normalise once at write time, then `1 - (a <=> b)` is the cosine and equals the dot product. Ablations on the two best chunk configs (paired bootstrap, dev, [section 2](../../eval/results/phase4/ablation_summary.md)):
no bge query prefix changes nDCG@5 by +0.014 [-0.013, +0.045] (hybrid) and -0.025 [-0.075, +0.021] (vector) on fixed_128_o30: a wash. The instruction prefix is documented to help asymmetric short-query-to-passage search, but this set
cannot detect it, so it stays on (the documented setting). arctic-xs vs bge: vector nDCG@5 -0.045 [-0.144, +0.059] and R@5 -0.115 [-0.222, -0.015] (significant); bge stays. Quantisation cost was not measured (no fp32 bge in the cache).
Query embedding costs 7.0 ms p50 (8.5 p95) on a cache miss and 0.09 ms on a hit (in-process LRU keyed by model, prefix and normalised text).

### 2.4 Vector search, HNSW, and "exact search is fine at this scale"
[bench/retrieval_index.py](../../bench/retrieval_index.py) compares HNSW with exact search on the production SQL, for (b) the current shared index (15 chunk configs, 1,616 bge vectors) and (a) an index holding only the active config
(190 vectors, built in a scratch schema and dropped afterwards). recall@10 against exact, 39 dev queries (all 15 configs for (b)); full table in [phase4-index.md](../../bench/results/phase4-index.md):

| scenario | iterative_scan | ef_search | recall@10 (all configs) | recall@10 (active config) | queries below 1.0 | p50 ms |
|---|---|---|---|---|---|---|
| (b) shared index | off | 40 | **0.274** | 0.444 | 100% | 0.68 |
| (b) shared index | off | 100 | 0.663 | 0.936 | 83% | 0.82 |
| (b) shared index | strict_order | 100 | 0.990 | 0.987 | 9.1% | 0.91 |
| (b) shared index | relaxed_order | 40 / 100 | 0.994 / **0.996** | 0.985 / 0.987 | 5.6% / 3.9% | 0.78 / 0.81 |
| (a) active only | off | 40 / 100 | 0.997 / 1.000 | 0.997 / 1.000 | 2.6% / 0 | 0.61 / 0.66 |
| exact (seq scan + sort) | - | - | 1.000 | 1.000 | 0 | 1.02 |
| planner, no steering flags | - | - | 1.000 | 1.000 | 0 | 0.73 |

**The post-filter recall pitfall.** The `chunk_config_id = X` condition is applied *after* HNSW returns its `ef_search` candidates. With 15 configs sharing the index only ~1/15 of the 40 candidates survive, so recall@10 collapses to 0.27.
pgvector 0.8 iterative scans keep walking the graph until enough rows pass the filter: 0.27 becomes 0.99 at no measurable latency cost (0.68 to 0.78-0.87 ms). `relaxed_order` can return rows slightly out of order, so [search.py](../../api/app/retrieval/search.py) re-sorts the survivors outside.
Pruning to the active config makes the problem disappear (0.997-1.000 with no iterative scan), which is why `ingest --prune` exists for production.
**Planner at small N.** For 100-1,600 rows the planner correctly chooses a btree scan on `chunk_config_id` plus a sort (0.73 ms), which is exact and *faster* than HNSW (0.8-0.9 ms); exact search is genuinely fine at this scale. The code steers the
planner to HNSW (`enable_seqscan/enable_sort=off`, `SET LOCAL`, reset after) only for the chunk-vector query so the ANN path is the one being studied and the one that keeps working when the corpus grows to 100k chunks. The steering is *scoped*: applying
`enable_sort=off` to the menu-item query too made it 70-110 ms (it needs a sort over 42 rows; seen during WP3 development, no saved artifact) and it is back to ~4 ms with default flags.

### 2.5 Hybrid search and RRF
`score(d) = sum over lists of 1 / (k + rank_list(d))`, k=60 ([fusion.py](../../api/app/retrieval/fusion.py)). Rank-based, so cosine in [0,1] and `ts_rank_cd` in [0,inf) never need calibrating. Ties break by best rank, then key (an RRF tie once made the
safety test order-dependent). k barely matters here: k = 10, 30, 60, 100 give nDCG@5 0.644 / 0.643 / 0.643 / 0.643 on fixed_128_o30 (interval of the k=10 difference
[-0.004, +0.006]): with two short lists the top of the fused list is decided by which items appear in both, not by how steeply ranks decay. The large effect is the FTS query operator. FTS alone: OR 0.629 R@5, AND 0.129 (a natural question has too many terms for
all of them to co-occur). Inside the hybrid it flips: AND is better, +0.050 nDCG@5 [-0.024, +0.141] on fixed_128_o30 and +0.021 on recursive_256_o15, because OR floods the fused list with noise and AND only contributes when the lexical match is
precise (it is mostly silent, so hybrid-AND behaves like vector search plus an exact-term rescue). Per category (nDCG@5, fixed_128_o30): FTS is useless for dietary rows (0.055; the signal is in structured columns, not words) and competitive on
FAQ/practices (0.65-0.67). On this corpus hybrid does **not** significantly beat vector-only (hybrid-AND vs vector: +0.009 [-0.025, +0.047]); at 40 dishes and ~190 chunks, embeddings alone already find the text. It is kept because lexical rescue matters when the corpus grows and costs 0.7 ms.

### 2.6 Reranking: quality gain vs ~1 s cost
The cross-encoder re-scores the fused top-N pairs. Dev, paired bootstrap vs hybrid: rerank top 10 +0.028 nDCG@5 [-0.046, +0.113]; top 20 -0.020 [-0.107, +0.069] on fixed_128_o30; on recursive_256_o15 -0.003 and -0.012. Test (final configs): prod 0.733, prod + rerank 10 **0.693**.
Cost, per stage ([section 5](../../eval/results/phase4/ablation_summary.md)): scoring is ~20 ms per 128-token chunk, so top 10 = 194 ms p50 (205 p95), top 20 = 396 ms p50 (412 p95), and with ~200-token chunks top 20 = 782 ms (841 p95). Host load was 4-6 on 10 CPUs throughout
(no quiet moment arrived, see 2.12), so treat these as upper-ish bounds. **I would not ship it for ranking**: a general web-passage cross-encoder does not know "menu" vs "FAQ", the budget is a 100 ms p95 retrieval, and nothing measured pays for 200-800 ms. Its one use is as an
abstention gate on the top 3 only: 60 ms p50 / 63 p95 (total 78 / 83 ms), see 2.10.

### 2.7 Metadata filtering: unsafe rate and why SQL
Filters are `WHERE` clauses on the live structured columns ([search.py](../../api/app/retrieval/search.py) `_item_clause`, `_chunk_where`); chunks linked to a violating item are dropped by `NOT EXISTS (... mi.id = ANY(ch.menu_item_ids) AND violation)`. The stress set ([filter_study.md](../../eval/results/phase4/filter_study.md)): 12 allergens and 5 dietary tags x 3 neutral templates = 51 queries, menu+docs scope, top 10:

| chunk config | mode | filters off: queries with an unsafe hit / unsafe hits | filters on |
|---|---|---|---|
| fixed_128_o30 | vector | 46 of 51 / 161 of 510 (31.6%) | **0 / 0** |
| fixed_128_o30 | hybrid | 45 of 51 / 117 of 510 (22.9%) | **0 / 0** |
| recursive_256_o15 | vector | 47 of 51 / 174 of 510 (34.1%) | **0 / 0** |
| recursive_256_o15 | hybrid | 45 of 51 / 163 of 510 (32.0%) | **0 / 0** |

On the 7 dev dietary rows (menu scope): unsafe hits 26 of 70 off, 0 of 70 on, and *recall improves* with filters on (hybrid R@5 0.250 to 0.464) because unsafe items no longer take the slots. **Why SQL and not a post-filter or "the model will not recommend it":** a post-filter on top-k
returns fewer than k (or nothing) when the top hits are unsafe, and similarity can never prove absence; a vector query has no notion of "contains peanuts" (allergens are deliberately not in the embedded text). Filters also do not give *relevance*: "gluten free main please"
returns House Kimchi first (all gluten-free, but not a main) because the intent word "main" is not a filter; Phase 5's tool should pass `categories`.
**The over-linking trade-off.** Dropping every chunk that *mentions* a violating dish is the safe direction, but a big chunk mentions many dishes. Per allergen filter, 512-token chunks lose 29-33% of the corpus and **35-39% of the safe evidence quotes lose every covering chunk** (max 55%);
fixed_128 loses 15% of chunks and 12.5% (max 29%) of safe quotes. This is a second, independent argument for small chunks, and it is not visible in plain recall.

### 2.8 Scope: why dietary rows search the menu only
First full run: dietary/allergen recall was 0.036 because FAQ and practices chunks about "gluten" and "vegan" outranked the structured items. Decision (user-approved): those rows carry `scope=["menu"]`. Ablation (`--ignore-row-scope`, [section 3](../../eval/results/phase4/ablation_summary.md)):
dietary R@5 0.464 (menu scope) vs 0.071 (menu+docs) for fixed_128_o30 hybrid; overall dev R@5 0.793 to 0.714. The filters still hold in both scopes (0 unsafe hits in 70).

### 2.9 Evaluation design: labels that survive re-chunking, splits, leakage
Labels are menu-item names plus verbatim evidence quotes resolved to character offsets in the cleaned document; a chunk counts as relevant when it covers at least 50% of a quote, so the same 94 quotes grade all 15 chunk configs (validated: every quote is covered by a chunk in every config).
Metrics ([evaluation.py](../../api/app/retrieval/evaluation.py)): relevance grade 2 (answers) / 1 (partial); recall@k = covered label **groups** / groups; success@k = 1 if any top-k hit is relevant; MRR = 1/rank of first relevant;
nDCG@k = sum over credited groups of (2^g - 1)/log2(rank + 1) divided by the same sum for the ideal ordering; context precision@k = relevant hits / k; budget recall = recall over the longest prefix of hits whose tokens fit 1,024.
**Grouped recall:** evidence that states the same fact (the dish guide and the PDF both give opening hours) shares a `group`; a group is covered when any member is; an ungrouped label is its own group. nDCG credits a group once (the first covering hit, at that hit's grade) and the ideal DCG uses the group's **maximum** grade, so duplicate alternates cannot inflate the ideal. The menu-item label is
*not* merged with its prose passages (merging would make every menu lookup chunk-config-blind). Tests: 3 new unit tests for grouped recall, group-max ideal and item groups.
**Splits and leakage.** Stratified by category with a fixed seed (39 dev / 26 test after dropping one row), test rows frozen by hash (`233cb205...2d`; the harness refuses `--split test` if any row changes). Queries were drafted in diner voice before seeing retrieval output; token Jaccard against evidence quotes is at most 0.32 (0 above 0.5, 0 with containment of 0.8
or more); the one copied-wording row (faq-05, containment 0.83) was reworded to "do i have to add a tip or is it already on there" before freezing. Dev was used for every choice; test was run **once**, after the production default was fixed, with the abstention thresholds taken from dev.
Honest limits: 35 answerable dev queries and 24 test queries; per-category n is 2-9; the no-answer sets are 4 (dev) and 2 (test).

### 2.10 Hallucination control: the "I don't know" signal for Phase 5
Three candidate signals, threshold chosen on dev to maximise balanced accuracy (BA), leave-one-out BA (LOO) for an honest estimate ([section 4](../../eval/results/phase4/ablation_summary.md)); dev has **4 no-answer queries**, so one query moves abstention by 25 points:

| signal (config) | threshold | answered (35 answerable) | abstained (4 no-answer) | BA in-sample | BA LOO | **test BA** (2 no-answer) |
|---|---|---|---|---|---|---|
| top-1 RRF score (hybrid-AND) | 0.0245 | 14.3% | 100% | 0.571 | 0.571 | 0.625 |
| best raw cosine (hybrid-AND) | 0.5978 | 97.1% | 75% | 0.861 | 0.736 | **0.500** (answered all, abstained 0 of 2) |
| best cross-encoder score, top 10 | -3.2575 | 68.6% | 100% | 0.843 | 0.718 | 0.917 |
| best cross-encoder score, top 3 (gate) | -3.2464 | 65.7% | 100% | 0.829 | 0.704 | not run on test |

RRF is not a confidence: it is rank arithmetic (a vector-only top-1 is always 1/61 = 0.0164, constant). Cosine looked fine on dev and **failed on test**: "loyalty card" and "calories in the pad thai" are near-misses by design, so their best cosine is above the dev threshold (the corpus has Pad Thai and gift cards).
Recommendation for Phase 5: gate "answer vs call_waiter" on the cross-encoder score of the top 3 hits (+60 ms p50; threshold about -3.25 in the scale of this model), log all three signals, never answer allergen questions from any similarity signal, and collect more no-answer queries
(a few dozen) before trusting a threshold. The gate abstains on 31-34% of answerable queries on dev, which is too many for a chat UX; the threshold is a starting point, not a result.

### 2.11 Latency and caching
Warm per-stage latency, in-process `retrieve()` against the compose Postgres, 3 warm-up calls then 5 repeats of each of 39 dev queries (195 samples), ms, p50 / p95 ([section 5](../../eval/results/phase4/ablation_summary.md)):

| config | embed | vector SQL | FTS SQL | fuse | rerank | total |
|---|---|---|---|---|---|---|
| vector, embed cache miss | 7.11 / 8.74 | 4.12 / 6.61 | - | 0.07 | - | 11.81 / 14.79 |
| FTS only | - | - | 1.07 / 1.88 | 0.02 | - | 1.39 / 2.28 |
| **prod (hybrid-AND), cache miss** | 6.98 / 8.52 | 4.03 / 5.02 | 0.70 / 0.97 | 0.07 | - | **12.19 / 14.18** |
| hybrid (OR), cache hit | 0.09 / 0.19 | 3.38 / 4.37 | 0.87 / 1.73 | 0.09 | - | 4.91 / 6.94 |
| prod + rerank top 3 | 7.04 / 8.70 | 8.63 / 11.22 | 0.95 / 1.53 | 0.08 | 60.2 / 63.2 | 77.6 / 83.1 |
| prod + rerank top 10 | 6.99 / 8.74 | 8.84 / 11.86 | 0.98 / 1.64 | 0.08 | 193.8 / 204.7 | 211.9 / 224.0 |

Budget check: retrieval p95 without rerank is 14.2 ms (miss) and about 5-7 ms (hit) against a 100 ms target. The embedding is 57% of a miss, so the LRU (1,024 entries, keyed by model + prefix + normalised text) removes most of the cost for repeated questions ("wifi password?");
it deliberately does not cache answers (that belongs with the LLM in Phase 5). Unexplained: the vector SQL stage doubles (4.0 to 8.8 ms) in the rerank runs; the ONNX rerank threads were probably still contending for CPU, but I did not isolate it, so those rows are not evidence about SQL cost.

### 2.12 Problems met on the way (problem, cause, fix)
- **Prices deleted as repeated headers** (2.1): digit-normalised repeat detection matched every price line; fix: edge lines only, price lines exempt, test added.
- **Stranded headings** (2.1): heading and body embedded apart; `glue_headings` + `PIPELINE_VERSION` "2".
- **Planner ignored HNSW at small N** (2.4): cheapest plan at 100 rows is exact; fix: `SET LOCAL enable_seqscan/enable_sort=off`, scoped to the chunk-vector query and reset in `finally`.
- **`enable_sort=off` made the menu query 70-110 ms** (2.4): applied to both queries first; scoped to the chunk query; back to ~4 ms.
- **RRF tie in the safety test**: a tie in the fused score made the test's expected order unstable; fix: deterministic tie-break (best rank, then key).
- **Stale container image for seed/migrate**: seed and migrate ran inside an image built before the new files/migration existed, so they used old code; fix: rebuild the image first (`make up` builds), then run them.
- **hf-xet needs a writable HOME**: the Hugging Face downloader failed without one in the container; fix: `HF_HOME` plus `FASTEMBED_CACHE_PATH=/models` on a named volume.
- **`explain_chunk_vector` test mismatch**: the EXPLAIN helper and the test disagreed about which plan is explained (steered vs unsteered); fix: the helper applies exactly the steering `retrieve()` uses.
- **Dietary recall 0.036** (2.8): docs crowded out menu items; fix: per-row `scope`.
- **Copied PDF wording in queries**: faq-05 contained 83% of its words from the FAQ; reworded.
- **NEW, the biggest: fake 100% HNSW recall at 0.87 ms** (index study, first run). Cause: asyncpg caches prepared statements by SQL text and Postgres switches to a generic plan after 5 executions; the exact-search ground-truth loop (seq scan plan) ran first, and the
  HNSW runs of the *same text* silently reused it. Reproduced in isolation: after 8 exact runs, `iterative_scan=off, ef=40` returned 10 rows instead of 1-5. Fix: a leading `/* chunk_vec:<index> */` comment in the production SQL (a test asserts the exact and HNSW texts differ), a per-(mode, schema) tag in the bench,
  and an assertion that the steered plan contains an HNSW scan. The same trap would have bitten `vector_index="exact"` in production.
- **Wrong run picked by tag**: the table script's glob `*_scope_both` also matched `nofilter_scope_both`, which showed "filters on" with unsafe hits identical to filters off. Caught because the two rows were *identical*; fix: exact tag match.
- **Defaults flipped, test broke**: changing `fts_operator` to AND made a contract test (`"corkage bottle fryer"` needs OR) return no hits; the test now states `fts_operator="or"`.
- **Dataset header double-nested** by my own edit script (`{"_meta": {"_meta": ...}}`), found when `--freeze` wrote the hash into the wrong level; flattened and refrozen (the test hash covers rows only and did not change).
- No escalation to `architect` was needed (nothing failed twice).

### 2.13 Where retrieval still fails (production config, per query)
Queries with recall@5 below 1 on dev (11 of 35) and test (9 of 24), from `per_query.jsonl` of the two final runs:
- **Dietary rows return the right *kind* of food, wrong *course*** (dev die-03, die-07, die-11; test die-09): "gluten free main please" ranks House Kimchi, Edamame, Hummus first; every one is gluten-free and safe, none is a main. The filter guarantees safety, not intent.
  die-11 ("egg allergy, what mains") has R@5 0.00 and R@10 0.44 against 9 labelled mains. Fix belongs to Phase 5: map the course word to the `categories` filter in the tool call.
- **Needs two documents, finds one** (dev faq-04 "table for 10", test com-03/com-04): recall@5 0.50-0.75; recall@10 reaches 1.0 for com-03 and com-04 (not for faq-04), so a larger k (10) under a token budget (1,024) is a better Phase 5 setting than k=5.
- **Vocabulary gap** (test faq-05 "tip" vs "gratuity"): R@5 0.50; the top hits are FAQ chunks and the policy chunk is missing. Query rewriting by the LLM is the cheap fix.
- **PDF-only facts** (dev pdf-06 "how much food should four of us order", test pdf-05 "swap dishes on the weekday lunch"): R@5 0.00; top hits are policy and FAQ chunks about parties and substitutions. The PDF's wording ("sharing menu", "set lunch") is not what the diner types.
- **Menu lookups where prose outranks the item card** (dev men-02, men-07): men-02 reaches R@10 1.0 but the Thai Green Curry card sits below rank 5; for men-07 one of the two label groups (the Mango Lassi card) is still missing at rank 10.

### 2.14 Questions RAG should not answer
Two kinds, both found by this eval rather than assumed. (1) **Aggregations and numeric orderings**: "what's the cheapest thing I can eat" was in the draft set (men-14) and was dropped by decision: `ORDER BY price_cents LIMIT 1` is a SQL question; an embedding of "cheapest" sits near "best value" prose
in the dish guide, which would be a confident, wrong-by-construction answer. The same family: "how many dishes under $15", "total for my cart". Phase 5 should route these to tools (`search_menu` filters, `get_item`), not to similarity. (2) **Negation and exclusion in prose**: die-05 "halal starters, not soup" returns Butternut Squash Soup in the top 3 (R@5 0.75) because
an embedding treats "not soup" as "soup". Structured filters handle allergens ("no dairy"), but free-text exclusions need the LLM to post-process filtered candidates. Both are documented as known limitations in [REVIEW.md](../../eval/datasets/REVIEW.md) rather than hidden in the metrics.

### 2.15 RAG vs fine-tuning, argued from this system
- **Freshness**: the menu changes daily (prices, availability, specials). Here a change to one document re-chunks only that document (hash check) and a menu edit is a row update with `is_available` filtered at query time; a fine-tuned model would need retraining and would still answer from stale weights.
- **Provenance**: each hit carries document, section path, page, chunk id and per-list ranks/scores, so an answer can cite and a wrong answer can be traced to a chunk; a fine-tuned model offers no source for a claim.
- **Safety**: allergen and dietary answers come from structured columns through SQL, which can be proven (0 of 510 unsafe hits) and audited; weights cannot be filtered per request, and the model must never "remember" that a dish is peanut-free.
- **Size and cost**: the corpus is ~14k words; retrieval costs 12 ms and $0 per query, no GPU, no training data. Fine-tuning would earn its keep for tone, tool-call formatting or refusing politely, which are behaviours, not facts.
- **Alternative to test**: with ~14k words the whole corpus nearly fits in a long context, so "stuff everything into the prompt" is a real baseline (latency, cost, lost-in-the-middle, no per-user filtering). Phase 7 measures RAG against full-menu-in-context instead of assuming.

### 2.16 Limitations to state in an interview
Small sets (35/24 answerable, 4/2 no-answer, 2-9 per category); one corpus written by the same author as the queries (style leakage is possible despite the Jaccard check); one embedding family and size; timings from a busy laptop, in-process (no HTTP/network hop; `/api/search` adds that); ground-truth quote labels are judgement calls (REVIEW.md lists them);
selection of the default used dev only, and test showed no improvement, so the default is a defensible engineering choice, not a proven winner.

### 2.17 What Phase 5 should take from this
- Call `retrieve()` with the defaults (`fixed_128_o30`, hybrid, FTS AND, no rerank), `k=10`, then cut to a 1,024-token context; this gave budget recall 0.884 on dev and 0.907 on test.
- `search_menu` = scope `menu` plus `exclude_allergens`, `dietary`, `categories` (the course word); `search_info` = scope `docs` (FAQ, policy, practices, PDF). Never send an allergy question to `search_info` alone.
- Decide "answer or `call_waiter`" with the top-3 cross-encoder score (threshold about -3.25 on dev), log it with the three other signals, and re-fit when there are 30+ no-answer queries.
- Use the Prometheus histograms (`retrieval_stage_seconds`) in the chat-turn latency budget; retrieval is 12 ms, so the LLM call will dominate by two orders of magnitude.
- Let the LLM rewrite vocabulary gaps ("tip" to "gratuity") and apply negations on the filtered candidates, since embeddings cannot.

## 3. Deployment, automation, monitoring, logging
- Compose: named volume `models:/models` with `FASTEMBED_CACHE_PATH=/models`; `api/entrypoint.sh` runs migrations on start; migration `0003_retrieval.py` (5 tables, GIN indexes, two partial HNSW indexes).
- Make targets: `make ingest ARGS=...` (`--config`, `--all-configs`, `--model`, `--menu`, `--docs`, `--prune`), `make menu-pdf`. Eval, safety, cleaning and index commands are in AGENTS.md.
- Metrics (Prometheus): `retrieval_stage_seconds{stage,mode}` histogram (embed, vector, fts, fuse, rerank, total) and `retrieval_query_embed_cache_total{result}`; exposed on `/metrics` next to the HTTP metrics. Debug endpoint `GET /api/search` returns hits with per-stage scores and timings.
- Logging: structlog JSON for ingest (per-document timings, counts), harness runs record argv, git SHA (dirty flag), corpus hash, model and library versions, host and (later runs) load average (`run.json`) so every number can be traced.
- Commands added this phase (also in AGENTS.md):
  - `cd api && uv run python ../eval/retrieval/run_retrieval_eval.py --validate | --freeze | --split dev|test --configs ...`
  - `cd api && uv run python ../eval/retrieval/phase4_tables.py` (all curated tables), `.../filter_study.py`, `.../pdf_cleaning.py`
  - `cd api && uv run python ../bench/retrieval_index.py` (HNSW study; builds and drops the scratch schema `idxstudy`)
  - harness flags added in WP6: `--no-filters`, `--embed-cache hit|miss`, `--ignore-row-scope`, `--threshold-from`, config keys `rrn` (rerank_top_n) and `fts`.
- Reproducibility: each run writes `eval/results/phase4/<run>/{run.json, per_query.jsonl, summary.json}`; `phase4_tables.py` regenerates every table; Phase 4 total compute for all runs was a few minutes, $0.

## Likely interview questions
1. **Why chunk at 128 tokens with 30% overlap, and how do you know?** The dev grid ranked it first (nDCG@5 0.685 vector, 0.694 hybrid-AND) and it has the best budget recall (0.884), but the intervals overlap and test did not confirm the lead (0.733 vs 0.748 for 256-token recursive). I chose it on dev point estimates plus a mechanism I can defend: smaller chunks over-drop less under allergen filters (12.5% vs 25.7% collateral label loss) and pack more evidence into a 1,024-token prompt. I would say that, and not claim it "won".
2. **What is the chunk-overlap trade-off?** Overlap repairs answers cut at a boundary (fixed_128 nDCG@5 0.572, 0.622, 0.685 for 0, 15, 30%) and costs index size (135, 157, 190 chunks) and near-duplicate hits; the nDCG credit-once-per-group rule stops duplicates inflating the score.
3. **Why does cosine equal the dot product here?** The embedder L2-normalises every vector; then `cos = a.b / (|a||b|) = a.b`, and pgvector `<=>` returns `1 - cos`. Normalising once removes the division and makes inner-product indexes valid.
4. **What goes wrong with HNSW plus a WHERE clause?** The filter runs after the ANN returns `ef_search` candidates. On the mixed 15-config index recall@10 was 0.27 (ef 40) and 0.66 (ef 100); `iterative_scan=relaxed_order` gave 0.994-0.996 at 0.8 ms. Alternatives: partial or per-tenant indexes, or pruning to one config (1.000).
5. **Why not just exact search?** At 190 to 1,600 rows exact is as fast (0.73 vs 0.8 ms, and the planner picks it by itself). I kept HNSW for the chunk query because it is the design that survives 100k chunks, and measured the recall cost; for the 42 menu items I use exact search after SQL filters so the allergen exclusion is guaranteed.
6. **Explain RRF and the k parameter.** Sum of `1/(k + rank)` across lists; needs no score calibration. k from 10 to 100 changed nDCG@5 by under 0.005 here because the lists are short; I would keep 60 (the standard) and say the parameter does not matter at this scale.
7. **Would you ship the reranker?** No for ranking: +0.028 nDCG@5 (interval -0.046 to +0.113) on dev, -0.040 on test, for 194 ms (top 10) to 782 ms (top 20 of 200-token chunks). Yes as an abstention gate on the top 3 (60 ms), pending more no-answer data.
8. **How do you stop the bot recommending a peanut dish to an allergic diner?** Allergen filters are SQL on structured columns, applied to menu items and to chunks linked to dishes. 0 unsafe hits in 510 across four configs vs 23-34% without. Similarity never decides safety, allergen text is not in the embeddings, and an unsure answer routes to `call_waiter`.
9. **How is your eval set protected from overfitting and leakage?** Offset-based labels, stratified dev/test split, test rows frozen by hash and refused if edited, query/evidence Jaccard check (max 0.32), reworded the one copied query, test run once. Result: the dev gain of +0.075 did not replicate (-0.015 [-0.112, +0.081]), which is exactly what the protocol exists to show.
10. **What is your abstention rule and how reliable is it?** For Phase 5, cross-encoder score of the top 3 against about -3.25, not cosine: cosine had BA 0.861 in-sample on dev and 0.500 on test (both test no-answer queries were near-misses). With 4 and 2 no-answer queries the confidence is low, so I report LOO BA (0.70-0.74) and plan to grow the set.
11. **RAG or fine-tuning for this chatbot?** RAG: the menu changes daily (prices, 86'd dishes, specials) and re-ingest changes 0 rows when nothing changed; answers need provenance (chunk ids, page, section); allergen safety must come from live structured columns, which a fine-tuned model cannot guarantee or update; and the corpus is ~14k words, small enough that retrieval is cheap (12 ms). Fine-tuning could help tone or tool-call format, not facts. Phase 7 will measure RAG against stuffing the whole menu into context.
12. **What should a RAG system refuse to answer?** Questions that are aggregations or numeric comparisons ("what is the cheapest thing"; that row was dropped from the eval because no embedding ranks by price, it is `ORDER BY price_cents LIMIT 1`) and negation ("halal starters, not soup": top hits include Butternut Squash Soup because embeddings treat "not X" as "X"; Phase 5's LLM applies the negation to the filtered candidates).

## Numbers
| What | Value | Source / command |
|---|---|---|
| Corpus | 6 docs, 11,207 words md + 4-page PDF (2,765 tokens cleaned, 3,927 raw), 42 dishes | `wc -w api/corpus/*.md`, [pdf_cleaning.md](../../eval/results/phase4/pdf_cleaning.md) |
| Eval set | 65 queries (39 dev / 26 test), 94 evidence quotes, test hash `233cb205...2d` | `cd api && uv run python ../eval/retrieval/run_retrieval_eval.py --validate` |
| Production default | `fixed_128_o30`, hybrid, FTS AND, no rerank, bge-small | [config.py](../../api/app/retrieval/config.py), [chunking.toml](../../api/app/retrieval/configs/chunking.toml) |
| Test split, prod (n=24 answerable) | R@1 0.397, R@5 0.832, R@10 0.907, succ@5 0.958, MRR 0.812, nDCG@5 0.733, budget recall 0.907 | [test run](../../eval/results/phase4/20261011T232653Z_test_test_final/summary.json) |
| Test split, old default (recursive_256_o15, OR) | R@5 0.889, MRR 0.793, nDCG@5 0.748 | same run |
| Test split, prod + rerank top 10 | R@5 0.752, MRR 0.785, nDCG@5 0.693 | same run |
| Dev, prod / old default | nDCG@5 0.694 / 0.618, R@5 0.814 / 0.793 | [final dev run](../../eval/results/phase4/20261011T232552Z_dev_final_dev/summary.json) |
| Paired bootstrap prod - old default | dev +0.075 [-0.020, +0.178]; test -0.015 [-0.112, +0.081] | [ablation_summary.md](../../eval/results/phase4/ablation_summary.md) 2b and 6 |
| Unsafe hits, filters off / on | 117-174 of 510 (23-34%) / 0 of 510; dev dietary rows 26 of 70 / 0 of 70 | [filter_study.md](../../eval/results/phase4/filter_study.md) |
| HNSW recall@10, shared index | 0.274 (off, ef 40), 0.663 (off, ef 100), 0.996 (relaxed, ef 100) | `cd api && uv run python ../bench/retrieval_index.py` |
| Retrieval latency, prod, p50 / p95 | 12.2 / 14.2 ms (miss), embed 57% of it; hit about 4.9 / 6.9 ms | [section 5](../../eval/results/phase4/ablation_summary.md) |
| Rerank latency p50 / p95 | top 3: 60 / 63 ms; top 10: 194 / 205; top 20: 396 / 412 (128-token) | same |
| PDF dish-in-one-chunk, fixed_128_o30 | raw 85.7%, cleaned 100% | `cd api && uv run python ../eval/retrieval/pdf_cleaning.py` |
| Host during all timings | 10 CPUs, load average 4.2 to 6.0 | `uptime`; not a quiet machine |
| Reproduce a dev run | `cd api && uv run python ../eval/retrieval/run_retrieval_eval.py --split dev --configs prod:chunk=fixed_128_o30,fts=and` | |
| Rebuild all tables | `cd api && uv run python ../eval/retrieval/phase4_tables.py` | |
