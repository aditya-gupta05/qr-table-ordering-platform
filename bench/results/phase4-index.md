# Phase 4: HNSW index study

Generated 2026-10-11T23:30:58.588446+00:00 by `bench/retrieval_index.py` (git a9119832). Host: macOS-26.6.2-arm64-arm-64bit, 10 CPUs, load average at end 3.89.

- Model BAAI/bge-small-en-v1.5, HNSW m=16, ef_construction=64, cosine ops, partial index per model.
- (b) all configs: 1616 rows in the bge index (3056 kB), 15 chunk configs; (a) active config only (fixed_128_o30): 190 rows, index 392 kB, built in 31.2 ms.
- 39 dev query texts, k=10. Ground truth = exact search with the same SQL and config predicate.
- recall columns: `all` = mean over every config x query in the scenario; `active` = the production config only. Latency = SQL only, 3 warm-up + 5 repeats per query, active config.

| scenario | iterative_scan | ef_search | recall@10 (all configs) | recall@10 (active) | worst query | queries < 1.0 | p50 ms | p95 ms |
|---|---|---|---|---|---|---|---|---|
| b_all_configs | off | 40 | 0.274 | 0.444 | 0.00 | 100.0% | 0.68 | 1.03 |
| b_all_configs | off | 100 | 0.663 | 0.936 | 0.00 | 83.4% | 0.82 | 1.07 |
| b_all_configs | strict_order | 40 | 0.978 | 0.980 | 0.80 | 18.5% | 0.87 | 1.33 |
| b_all_configs | strict_order | 100 | 0.990 | 0.987 | 0.80 | 9.1% | 0.91 | 1.28 |
| b_all_configs | relaxed_order | 40 | 0.994 | 0.985 | 0.80 | 5.6% | 0.78 | 1.01 |
| b_all_configs | relaxed_order | 100 | 0.996 | 0.987 | 0.80 | 3.9% | 0.81 | 1.15 |
| a_active_only | off | 40 | 0.997 | 0.997 | 0.90 | 2.6% | 0.61 | 0.72 |
| a_active_only | off | 100 | 1.000 | 1.000 | 1.00 | 0.0% | 0.66 | 0.89 |
| a_active_only | strict_order | 40 | 0.997 | 0.997 | 0.90 | 2.6% | 0.61 | 0.81 |
| a_active_only | strict_order | 100 | 1.000 | 1.000 | 1.00 | 0.0% | 0.70 | 1.19 |
| a_active_only | relaxed_order | 40 | 0.997 | 0.997 | 0.90 | 2.6% | 0.70 | 1.04 |
| a_active_only | relaxed_order | 100 | 1.000 | 1.000 | 1.00 | 0.0% | 0.73 | 1.12 |

Exact (index scans off, seq scan + sort), active config: p50 1.02 ms, p95 1.44 ms.
Planner with NO steering flags, active config: p50 0.73 ms, p95 1.27 ms.

## Plans (EXPLAIN, one dev query)

### planner_default_all_configs: exact: scan + sort (no ANN index)

```
Limit  (cost=782.66..782.68 rows=10 width=861)
  InitPlan 1 (returns $0)
    ->  Index Scan using chunk_configs_name_key on chunk_configs  (cost=0.15..8.17 rows=1 width=4)
          Index Cond: (name = 'fixed_128_o30'::text)
  ->  Sort  (cost=774.49..774.79 rows=120 width=861)
        Sort Key: ((e.embedding <=> '[q]'::vector))
        ->  Nested Loop  (cost=5.55..771.90 rows=120 width=861)
              ->  Nested Loop  (cost=5.27..219.19 rows=108 width=853)
                    ->  Bitmap Heap Scan on chunks ch  (cost=5.11..212.27 rows=108 width=789)
                          Recheck Cond: (chunk_config_id = $0)
                          ->  Bitmap Index Scan on ix_chunks_chunk_config_id  (cost=0.00..5.09 rows=108 width=0)
                                Index Cond: (chunk_config_id = $0)
                    ->  Memoize  (cost=0.16..0.62 rows=1 width=68)
                          Cache Key: ch.document_id
                          Cache Mode: logical
                          ->  Index Scan using documents_pkey on documents d  (cost=0.15..0.61 rows=1 width=68)
                                Index Cond: (id = ch.document_id)
              ->  Index Scan using chunk_embeddings_pkey on chunk_embeddings e  (cost=0.28..5.11 rows=1 width=1552)
                    Index Cond: ((chunk_id = ch.id) AND (model = 'BAAI/bge-small-en-v1.5'::text))
```

### steered_hnsw_all_configs: HNSW index scan

```
Limit  (cost=836.48..1276.31 rows=10 width=861)
  InitPlan 1 (returns $0)
    ->  Index Scan using chunk_configs_name_key on chunk_configs  (cost=0.15..8.17 rows=1 width=4)
          Index Cond: (name = 'fixed_128_o30'::text)
  ->  Nested Loop  (cost=828.32..6106.29 rows=120 width=861)
        ->  Nested Loop  (cost=828.16..6098.76 rows=120 width=2333)
              ->  Index Scan using ix_chunk_emb_hnsw_bge_small on chunk_embeddings e  (cost=827.87..4790.08 rows=1796 width=1552)
                    Order By: (embedding <=> '[q]'::vector)
              ->  Memoize  (cost=0.29..0.93 rows=1 width=789)
                    Cache Key: e.chunk_id
                    Cache Mode: logical
                    ->  Index Scan using chunks_pkey on chunks ch  (cost=0.28..0.92 rows=1 width=789)
                          Index Cond: (id = e.chunk_id)
                          Filter: (chunk_config_id = $0)
        ->  Memoize  (cost=0.16..0.62 rows=1 width=68)
              Cache Key: ch.document_id
              Cache Mode: logical
              ->  Index Scan using documents_pkey on documents d  (cost=0.15..0.61 rows=1 width=68)
                    Index Cond: (id = ch.document_id)
```

### planner_default_active_only: exact: scan + sort (no ANN index)

```
Limit  (cost=209.59..209.62 rows=10 width=644)
  InitPlan 1 (returns $0)
    ->  Seq Scan on chunk_configs  (cost=0.00..1.01 rows=1 width=4)
          Filter: (name = 'fixed_128_o30'::text)
  ->  Sort  (cost=208.58..209.06 rows=190 width=644)
        Sort Key: ((e.embedding <=> '[q]'::vector))
        ->  Hash Join  (cost=69.89..204.47 rows=190 width=644)
              Hash Cond: (ch.document_id = d.id)
              ->  Hash Join  (cost=68.75..202.01 rows=190 width=2159)
                    Hash Cond: (e.chunk_id = ch.id)
                    ->  Seq Scan on chunk_embeddings e  (cost=0.00..132.75 rows=190 width=1552)
                          Filter: (model = 'BAAI/bge-small-en-v1.5'::text)
                    ->  Hash  (cost=66.38..66.38 rows=190 width=615)
                          ->  Seq Scan on chunks ch  (cost=0.00..66.38 rows=190 width=615)
                                Filter: (chunk_config_id = $0)
              ->  Hash  (cost=1.06..1.06 rows=6 width=25)
                    ->  Seq Scan on documents d  (cost=0.00..1.06 rows=6 width=25)
```

### steered_hnsw_active_only: HNSW index scan

```
Limit  (cost=205.87..241.27 rows=10 width=644)
  InitPlan 1 (returns $0)
    ->  Index Scan using chunk_configs_name_idx on chunk_configs  (cost=0.12..8.14 rows=1 width=4)
          Index Cond: (name = 'fixed_128_o30'::text)
  ->  Nested Loop  (cost=197.72..870.45 rows=190 width=644)
        ->  Nested Loop  (cost=197.58..864.14 rows=190 width=2159)
              ->  Index Scan using ix_study_hnsw on chunk_embeddings e  (cost=197.43..639.33 rows=190 width=1552)
                    Order By: (embedding <=> '[q]'::vector)
              ->  Memoize  (cost=0.15..1.56 rows=1 width=615)
                    Cache Key: e.chunk_id
                    Cache Mode: logical
                    ->  Index Scan using chunks_pkey on chunks ch  (cost=0.14..1.55 rows=1 width=615)
                          Index Cond: (id = e.chunk_id)
                          Filter: (chunk_config_id = $0)
        ->  Memoize  (cost=0.14..0.22 rows=1 width=25)
              Cache Key: ch.document_id
              Cache Mode: logical
              ->  Index Scan using documents_pkey on documents d  (cost=0.13..0.21 rows=1 width=25)
                    Index Cond: (id = ch.document_id)
```

