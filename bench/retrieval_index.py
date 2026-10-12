# ruff: noqa: E501, ASYNC230, ASYNC240
"""HNSW index study (Phase 4, WP6): does the ANN index return what exact search returns, and at what cost?

Run (stack up, DB ingested):
    cd api && uv run python ../bench/retrieval_index.py
Output: bench/results/phase4-index.json and phase4-index.md

Question 1: recall@10 of HNSW vs exact search for the PRODUCTION query (app.retrieval.search.chunk_vector_sql)
  scenario (b) "all": the shared index holds every chunk config x model that is ingested (production layout
      during ablations): the `chunk_config_id = X` predicate is a POST-filter on the ANN candidates
  scenario (a) "active": an index that holds only the active config's chunks (the layout after
      `ingest --prune`). Built in a scratch schema `idxstudy` (copies of documents/chunk_configs/chunks/
      chunk_embeddings with the same HNSW parameters); nothing in `public` is touched; the schema is dropped at the end.
  settings: iterative_scan {off, strict_order, relaxed_order} x ef_search {40, 100}
Question 2: latency of each setting (SQL only, query vector precomputed; 3 warm-up + 5 repeats per query).
Question 3: which plan does the planner pick WITHOUT steering flags at this table size (EXPLAIN).

Ground truth = exact search (enable_indexscan/bitmapscan off) with the same SQL and the same config predicate.
recall@10 = |HNSW top-10 ∩ exact top-10| / |exact top-10|, averaged over queries (dev-split query texts).
"""

import argparse
import asyncio
import json
import os
import platform
import re
import statistics
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "api"))

from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine  # noqa: E402

from app.retrieval.config import Filters, RetrievalConfig  # noqa: E402
from app.retrieval.embedder import get_embedder, to_pgvector  # noqa: E402
from app.retrieval.search import PLANNER_FLAGS, _set_local, chunk_vector_sql  # noqa: E402

DB_URL = os.environ.get("DATABASE_URL", "postgresql+asyncpg://qr:qr@localhost:55432/qr")
DATASET = REPO / "eval" / "datasets" / "retrieval_v1.jsonl"
OUT = REPO / "bench" / "results"
MODEL = "BAAI/bge-small-en-v1.5"
ACTIVE = "fixed_128_o30"
K = 10


def git_sha() -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=REPO, capture_output=True, text=True
    ).stdout.strip()


def percentile(values: list[float], p: float) -> float:
    s = sorted(values)
    i = (len(s) - 1) * p
    lo, hi = int(i), min(int(i) + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (i - int(i))


def dev_queries() -> list[str]:
    qs = []
    for line in DATASET.read_text().splitlines():
        r = json.loads(line)
        if "_meta" not in r and r["split"] == "dev":
            qs.append(r["query"])
    return qs


async def run_sql(
    conn: AsyncConnection, cfg: RetrievalConfig, qvec: str, mode: str, schema: str | None
):
    """mode: 'ann' (steered to HNSW), 'exact' (index scans off), 'planner' (no steering).
    Returns (ids, ms)."""
    sql, p = chunk_vector_sql(cfg, Filters(available_only=False))
    # Distinct statement text per (mode, schema): asyncpg caches prepared statements by text and Postgres
    # reuses a generic plan after 5 runs, which silently replayed a seq-scan plan for the HNSW runs.
    sql = f"/* study:{mode}:{schema or 'public'} */ " + sql
    p["q"] = qvec
    p["n"] = K
    async with conn.begin():
        local = {
            "hnsw.ef_search": str(cfg.ef_search),
            "hnsw.iterative_scan": cfg.iterative_scan,
        }
        if mode == "ann":
            local |= {"enable_seqscan": "off", "enable_sort": "off"}
        elif mode == "exact":
            local |= {"enable_indexscan": "off", "enable_bitmapscan": "off"}
        await _set_local(conn, local)
        if schema:
            await conn.execute(text(f"SET LOCAL search_path = {schema}, public"))
        t = time.perf_counter()
        res = (await conn.execute(text(sql), p)).all()
        ms = (time.perf_counter() - t) * 1000
        await _set_local(conn, dict.fromkeys(PLANNER_FLAGS, "on"))
    return [r.id for r in res], ms


async def explain(
    conn: AsyncConnection, cfg: RetrievalConfig, qvec: str, mode: str, schema: str | None
) -> str:
    sql, p = chunk_vector_sql(cfg, Filters(available_only=False))
    p["q"] = qvec
    p["n"] = K
    async with conn.begin():
        local = {"hnsw.ef_search": str(cfg.ef_search), "hnsw.iterative_scan": cfg.iterative_scan}
        if mode == "ann":
            local |= {"enable_seqscan": "off", "enable_sort": "off"}
        await _set_local(conn, local)
        if schema:
            await conn.execute(text(f"SET LOCAL search_path = {schema}, public"))
        rows = (await conn.execute(text("EXPLAIN " + sql), p)).all()
    return re.sub(r"'\[[-0-9.e, ]+\]'::vector", "'[q]'::vector", "\n".join(r[0] for r in rows))


def uses(plan: str) -> str:
    if "hnsw" in plan.lower():
        return "HNSW index scan"
    if "Sort" in plan:
        return "exact: scan + sort (no ANN index)"
    return "other"


async def build_active_schema(conn: AsyncConnection, config: str) -> dict:
    await conn.execute(text("DROP SCHEMA IF EXISTS idxstudy CASCADE"))
    await conn.execute(text("CREATE SCHEMA idxstudy"))
    await conn.execute(text("CREATE TABLE idxstudy.documents AS SELECT * FROM public.documents"))
    await conn.execute(
        text(
            "CREATE TABLE idxstudy.chunk_configs AS SELECT * FROM public.chunk_configs WHERE name = :n"
        ),
        {"n": config},
    )
    await conn.execute(
        text(
            "CREATE TABLE idxstudy.chunks AS SELECT ch.* FROM public.chunks ch "
            "WHERE ch.chunk_config_id = (SELECT id FROM public.chunk_configs WHERE name = :n)"
        ),
        {"n": config},
    )
    await conn.execute(
        text(
            "CREATE TABLE idxstudy.chunk_embeddings AS SELECT e.* FROM public.chunk_embeddings e "
            "JOIN idxstudy.chunks c ON c.id = e.chunk_id"
        )
    )
    # same supporting indexes as public, otherwise the steered plan joins by nested seq scans
    for stmt in (
        "ALTER TABLE idxstudy.documents ADD PRIMARY KEY (id)",
        "ALTER TABLE idxstudy.chunk_configs ADD PRIMARY KEY (id)",
        "ALTER TABLE idxstudy.chunks ADD PRIMARY KEY (id)",
        "ALTER TABLE idxstudy.chunk_embeddings ADD PRIMARY KEY (chunk_id, model)",
        "CREATE INDEX ON idxstudy.chunks (chunk_config_id)",
        "CREATE UNIQUE INDEX ON idxstudy.chunk_configs (name)",
    ):
        await conn.execute(text(stmt))
    t = time.perf_counter()
    await conn.execute(
        text(
            "CREATE INDEX ix_study_hnsw ON idxstudy.chunk_embeddings USING hnsw (embedding vector_cosine_ops) "
            f"WITH (m = 16, ef_construction = 64) WHERE (model = '{MODEL}')"
        )
    )
    build_ms = (time.perf_counter() - t) * 1000
    for t_ in ("documents", "chunk_configs", "chunks", "chunk_embeddings"):
        await conn.execute(text(f"ANALYZE idxstudy.{t_}"))
    n = (
        await conn.execute(
            text("SELECT count(*) FROM idxstudy.chunk_embeddings WHERE model = :m"), {"m": MODEL}
        )
    ).scalar_one()
    size = (
        await conn.execute(text("SELECT pg_relation_size('idxstudy.ix_study_hnsw')"))
    ).scalar_one()
    return {"rows": n, "index_build_ms": round(build_ms, 1), "index_bytes": size}


async def main_async(args: argparse.Namespace) -> None:
    engine = create_async_engine(DB_URL)
    queries = dev_queries()
    emb = get_embedder(MODEL)
    qvecs = [to_pgvector(emb.embed_query(q)) for q in queries]
    print(f"{len(queries)} dev queries embedded")

    async with engine.connect() as conn:
        cfgs = [
            r.name
            for r in await conn.execute(
                text(
                    "SELECT DISTINCT cc.name FROM chunk_configs cc JOIN chunks c ON c.chunk_config_id = cc.id "
                    "JOIN chunk_embeddings e ON e.chunk_id = c.id WHERE e.model = :m ORDER BY cc.name"
                ),
                {"m": MODEL},
            )
        ]
        n_all = (
            await conn.execute(
                text("SELECT count(*) FROM chunk_embeddings WHERE model = :m"), {"m": MODEL}
            )
        ).scalar_one()
        n_active_cfg = (
            await conn.execute(
                text(
                    "SELECT count(*) FROM chunk_embeddings e JOIN chunks c ON c.id = e.chunk_id "
                    "JOIN chunk_configs cc ON cc.id = c.chunk_config_id WHERE e.model = :m AND cc.name = :n"
                ),
                {"m": MODEL, "n": ACTIVE},
            )
        ).scalar_one()
        idx_size_all = (
            await conn.execute(text("SELECT pg_relation_size('ix_chunk_emb_hnsw_bge_small')"))
        ).scalar_one()
        await conn.commit()

        built = await build_active_schema(conn, ACTIVE)
        await conn.commit()
        print("active-only schema:", built)

        results: list[dict] = []
        plans: dict[str, str] = {}
        # guard: the steered statement must really use the HNSW index in both scenarios
        for sc in (None, "idxstudy"):
            plan = await explain(
                conn, RetrievalConfig(chunk_config=ACTIVE, k=K), qvecs[0], "ann", sc
            )
            assert "hnsw" in plan.lower(), f"steered plan does not use HNSW ({sc}):\n{plan[:400]}"
        try:
            exact: dict[tuple[str, int], list[int]] = {}
            exact_ms: dict[str, list[float]] = {}
            for cname in cfgs:
                base = RetrievalConfig(chunk_config=cname, k=K)
                for qi, qv in enumerate(qvecs):
                    ids, ms = await run_sql(conn, base, qv, "exact", None)
                    exact[(cname, qi)] = ids
                    if cname == ACTIVE:
                        exact_ms.setdefault("exact", []).append(ms)
            print("exact ground truth computed for", len(cfgs), "configs")

            def settings():
                for it in ("off", "strict_order", "relaxed_order"):
                    for ef in (40, 100):
                        yield it, ef

            for scenario, schema, config_list in (
                ("b_all_configs", None, cfgs),
                ("a_active_only", "idxstudy", [ACTIVE]),
            ):
                for it, ef in settings():
                    recs, active_recs, lat = [], [], []
                    for cname in config_list:
                        cfg = RetrievalConfig(
                            chunk_config=cname, k=K, ef_search=ef, iterative_scan=it
                        )  # type: ignore[arg-type]
                        for qi, qv in enumerate(qvecs):
                            if (
                                cname == ACTIVE
                            ):  # warm-up + repeats only for the active config (headline latency)
                                for _ in range(3):
                                    await run_sql(conn, cfg, qv, "ann", schema)
                                for _ in range(5):
                                    ids, ms = await run_sql(conn, cfg, qv, "ann", schema)
                                    lat.append(ms)
                            else:
                                ids, _ = await run_sql(conn, cfg, qv, "ann", schema)
                            truth = exact[(cname, qi)]
                            r = len(set(ids) & set(truth)) / len(truth) if truth else 1.0
                            recs.append(r)
                            if cname == ACTIVE:
                                active_recs.append(r)
                    results.append(
                        {
                            "scenario": scenario,
                            "iterative_scan": it,
                            "ef_search": ef,
                            "n_queries_recall": len(recs),
                            "recall@10_mean": round(statistics.fmean(recs), 4),
                            "recall@10_min": round(min(recs), 4),
                            "recall@10_active_config": round(statistics.fmean(active_recs), 4),
                            "share_queries_below_1": round(
                                sum(1 for r in recs if r < 1.0) / len(recs), 4
                            ),
                            "latency_ms_active": {
                                "p50": round(percentile(lat, 0.5), 3),
                                "p95": round(percentile(lat, 0.95), 3),
                                "n": len(lat),
                            },
                        }
                    )
                    print(results[-1])

            # exact baseline latency + planner choice
            exact_lat = []
            base = RetrievalConfig(chunk_config=ACTIVE, k=K)
            for qv in qvecs:
                for _ in range(3):
                    await run_sql(conn, base, qv, "exact", None)
                for _ in range(5):
                    exact_lat.append((await run_sql(conn, base, qv, "exact", None))[1])
            planner_lat = []
            for qv in qvecs:
                for _ in range(3):
                    await run_sql(conn, base, qv, "planner", None)
                for _ in range(5):
                    planner_lat.append((await run_sql(conn, base, qv, "planner", None))[1])
            plans["planner_default_all_configs"] = await explain(
                conn, base, qvecs[0], "planner", None
            )
            plans["steered_hnsw_all_configs"] = await explain(conn, base, qvecs[0], "ann", None)
            plans["planner_default_active_only"] = await explain(
                conn, base, qvecs[0], "planner", "idxstudy"
            )
            plans["steered_hnsw_active_only"] = await explain(
                conn, base, qvecs[0], "ann", "idxstudy"
            )
        finally:
            async with engine.connect() as c2:
                await c2.execute(text("DROP SCHEMA IF EXISTS idxstudy CASCADE"))
                await c2.commit()

    def lat(v: list[float]) -> dict:
        return {
            "p50": round(percentile(v, 0.5), 3),
            "p95": round(percentile(v, 0.95), 3),
            "n": len(v),
        }

    out = {
        "generated_utc": datetime.now(UTC).isoformat(),
        "host": {
            "platform": platform.platform(),
            "cpus": os.cpu_count(),
            "loadavg": os.getloadavg(),
        },
        "git_sha": git_sha(),
        "model": MODEL,
        "active_config": ACTIVE,
        "hnsw": {"m": 16, "ef_construction": 64},
        "index_rows_all_configs": n_all,
        "index_bytes_all_configs": idx_size_all,
        "rows_active_config": n_active_cfg,
        "active_only_index": built,
        "n_configs_in_b": len(cfgs),
        "n_queries": len(queries),
        "k": K,
        "results": results,
        "exact_seq_scan_latency_ms_active": lat(exact_lat),
        "planner_default_latency_ms_active": lat(planner_lat),
        "plans": plans,
        "planner_default_choice": {k: uses(v) for k, v in plans.items()},
    }
    OUT.mkdir(exist_ok=True)
    (OUT / "phase4-index.json").write_text(json.dumps(out, indent=2))
    (OUT / "phase4-index.md").write_text(render(out))
    print("wrote bench/results/phase4-index.{json,md}")
    await engine.dispose()


def render(o: dict) -> str:
    L = [
        "# Phase 4: HNSW index study",
        "",
        f"Generated {o['generated_utc']} by `bench/retrieval_index.py` (git {o['git_sha'][:8]}). Host: {o['host']['platform']}, "
        f"{o['host']['cpus']} CPUs, load average at end {o['host']['loadavg'][0]:.2f}.",
        "",
        f"- Model {o['model']}, HNSW m={o['hnsw']['m']}, ef_construction={o['hnsw']['ef_construction']}, cosine ops, partial index per model.",
        f"- (b) all configs: {o['index_rows_all_configs']} rows in the bge index ({o['index_bytes_all_configs'] / 1024:.0f} kB), {o['n_configs_in_b']} chunk configs; "
        f"(a) active config only ({o['active_config']}): {o['rows_active_config']} rows, index {o['active_only_index']['index_bytes'] / 1024:.0f} kB, built in {o['active_only_index']['index_build_ms']} ms.",
        f"- {o['n_queries']} dev query texts, k={o['k']}. Ground truth = exact search with the same SQL and config predicate.",
        "- recall columns: `all` = mean over every config x query in the scenario; `active` = the production config only. Latency = SQL only, 3 warm-up + 5 repeats per query, active config.",
        "",
        "| scenario | iterative_scan | ef_search | recall@10 (all configs) | recall@10 (active) | worst query | queries < 1.0 | p50 ms | p95 ms |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for r in o["results"]:
        L.append(
            f"| {r['scenario']} | {r['iterative_scan']} | {r['ef_search']} | {r['recall@10_mean']:.3f} | {r['recall@10_active_config']:.3f} "
            f"| {r['recall@10_min']:.2f} | {r['share_queries_below_1']:.1%} | {r['latency_ms_active']['p50']:.2f} | {r['latency_ms_active']['p95']:.2f} |"
        )
    L += [
        "",
        f"Exact (index scans off, seq scan + sort), active config: p50 {o['exact_seq_scan_latency_ms_active']['p50']:.2f} ms, p95 {o['exact_seq_scan_latency_ms_active']['p95']:.2f} ms.",
        f"Planner with NO steering flags, active config: p50 {o['planner_default_latency_ms_active']['p50']:.2f} ms, p95 {o['planner_default_latency_ms_active']['p95']:.2f} ms.",
        "",
        "## Plans (EXPLAIN, one dev query)",
        "",
    ]
    for name, plan in o["plans"].items():
        L += [f"### {name}: {o['planner_default_choice'][name]}", "", "```", plan, "```", ""]
    return "\n".join(L) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    asyncio.run(main_async(ap.parse_args()))


if __name__ == "__main__":
    main()
