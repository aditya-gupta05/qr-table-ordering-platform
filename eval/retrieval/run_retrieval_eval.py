# ruff: noqa: E501, ASYNC230, ASYNC240  (CLI script: long prose strings, blocking file I/O is fine)
"""Retrieval eval harness (Phase 4, WP5). Run from api/ so the app package and its venv are used:

  cd api
  uv run python ../eval/retrieval/run_retrieval_eval.py --validate [--review]
  uv run python ../eval/retrieval/run_retrieval_eval.py --split dev --configs default
  uv run python ../eval/retrieval/run_retrieval_eval.py --split dev \
      --configs default "rr:rerank=1" "vec:mode=vector" "f256:chunk=fixed_256_o15"
  uv run python ../eval/retrieval/run_retrieval_eval.py --freeze      # after the user reviewed REVIEW.md

A config is `label` or `label:key=value,key=value`. Keys: chunk, mode (vector|fts|hybrid), rerank
(0|1), model (bge|arctic|<full name>), rrf_k, prefix (default|none|<text>), fts (or|and),
index (hnsw|exact), ef, cand, k, rrn (rerank_top_n). Unset keys use RetrievalConfig defaults (hybrid, no rerank,
recursive_256_o15, bge). prepend_headings is a property of the chunk config, not of this flag set.

Outputs: eval/results/phase4/<run_id>/{run.json, per_query.jsonl, summary.json} and a regenerated
eval/results/phase4/all_runs.md (raw table of every run; the curated tables are made by phase4_tables.py).

The test split is locked until the dataset header says frozen=true and the stored hash still matches:
tune on dev, freeze, then report test once.
"""

import argparse
import asyncio
import hashlib
import importlib.metadata as md
import json
import os
import platform
import re
import subprocess
import sys
import time
from collections import Counter, defaultdict
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "api"))

import structlog  # noqa: E402
from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine  # noqa: E402

from app.retrieval import evaluation as ev  # noqa: E402
from app.retrieval.cache import QueryEmbedCache  # noqa: E402
from app.retrieval.chunking import TokenCounter  # noqa: E402
from app.retrieval.config import Filters, RetrievalConfig  # noqa: E402
from app.retrieval.embedder import load_chunk_tokenizer  # noqa: E402
from app.retrieval.parse import ParsedDoc, load_corpus  # noqa: E402
from app.retrieval.search import RetrievalResult, retrieve  # noqa: E402
from app.retrieval.settings import get_retrieval_settings  # noqa: E402
from app.vocab import ALLERGENS, DIETARY_TAGS  # noqa: E402

log = structlog.get_logger("retrieval.eval")
DATASET = REPO / "eval" / "datasets" / "retrieval_v1.jsonl"
REVIEW = REPO / "eval" / "datasets" / "REVIEW.md"
RESULTS = REPO / "eval" / "results" / "phase4"
DB_URL = os.environ.get("DATABASE_URL", "postgresql+asyncpg://qr:qr@localhost:55432/qr")
MODEL_ALIASES = {"bge": "BAAI/bge-small-en-v1.5", "arctic": "snowflake/snowflake-arctic-embed-xs"}
STAGES = ("embed", "vector", "fts", "fuse", "rerank", "total")
RETRIEVE_K = 10
JACCARD_FLAG = 0.5
CONTAIN_FLAG = 0.8
CATEGORIES = (
    "menu_lookup",
    "dietary_allergen",
    "faq_policy",
    "kitchen_practices",
    "pdf_only",
    "comparison",
    "no_answer",
)


# --- dataset --------------------------------------------------------------------------------------


def load_dataset(path: Path = DATASET) -> tuple[dict, list[dict]]:
    meta, rows = {}, []
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        obj = json.loads(line)
        if "_meta" in obj:
            meta = obj["_meta"]
        else:
            rows.append(obj)
    return meta, rows


def test_hash(rows: list[dict]) -> str:
    test = sorted((r for r in rows if r["split"] == "test"), key=lambda r: r["id"])
    return hashlib.sha256(json.dumps(test, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def build_labels(row: dict, docs: dict[str, ParsedDoc]) -> ev.Labels:
    spans = []
    for e in row["evidence"]:
        doc = docs.get(e["doc_slug"])
        if doc is None:
            raise ev.QuoteError(f"{row['id']}: unknown doc_slug {e['doc_slug']!r}")
        s, t = ev.resolve_quote(doc.content, e["quote"])
        spans.append(ev.EvidenceSpan(e["doc_slug"], s, t, int(e["grade"]), e.get("group")))
    return ev.Labels(
        {n: 2 for n in row["relevant_menu_item_names"]},
        tuple(spans),
        dict(row.get("item_groups") or {}),
    )


# --- validation -----------------------------------------------------------------------------------


async def fetch_menu(engine: AsyncEngine) -> dict[int, dict]:
    async with engine.connect() as c:
        rows = await c.execute(
            text("SELECT id, name, allergens, dietary_tags, is_available FROM menu_items")
        )
        return {
            r.id: {
                "name": r.name,
                "allergens": set(r.allergens),
                "dietary": set(r.dietary_tags),
                "available": r.is_available,
            }
            for r in rows
        }


def violates(item: dict, f: Filters) -> bool:
    return (
        (f.available_only and not item["available"])
        or bool(item["allergens"] & set(f.exclude_allergens))
        or not set(f.dietary) <= item["dietary"]
    )


async def validate(engine: AsyncEngine, meta: dict, rows: list[dict]) -> dict:
    """Everything checkable without running retrieval. Returns a report; `errors` must be empty."""
    errors: list[str] = []
    docs = {d.slug: d for d in load_corpus(get_retrieval_settings().corpus_dir)}
    menu = await fetch_menu(engine)
    by_name = {m["name"]: i for i, m in menu.items()}
    ids = [r["id"] for r in rows]
    for i, n in Counter(ids).items():
        if n > 1:
            errors.append(f"duplicate id {i}")

    async with engine.connect() as c:
        db_docs = {
            r.slug: r.content_hash
            for r in await c.execute(text("SELECT slug, content_hash FROM documents"))
        }
        cfgs = [
            r.name for r in await c.execute(text("SELECT name FROM chunk_configs ORDER BY name"))
        ]
    for slug, d in docs.items():
        if db_docs.get(slug) != d.content_hash:
            errors.append(
                f"document {slug!r} in the DB is not the current corpus; re-run make ingest"
            )

    labels: dict[str, ev.Labels] = {}
    quote_rows = []  # (row id, slug, start, end, grade, jaccard, containment)
    for r in rows:
        rid = r["id"]
        if r["category"] not in CATEGORIES:
            errors.append(f"{rid}: bad category {r['category']!r}")
        if not set(r.get("scope") or ["menu", "docs"]) <= {"menu", "docs"}:
            errors.append(f"{rid}: bad scope {r.get('scope')!r}")
        if r["split"] not in ("dev", "test"):
            errors.append(f"{rid}: bad split {r['split']!r}")
        f = r.get("filters") or {}
        bad = set(f.get("exclude_allergens", [])) - set(ALLERGENS)
        bad |= set(f.get("dietary", [])) - set(DIETARY_TAGS)
        bad |= set(f) - {"exclude_allergens", "dietary"}
        if bad:
            errors.append(f"{rid}: bad filter values {sorted(bad)}")
        try:
            labels[rid] = build_labels(r, docs)
        except ev.QuoteError as e:
            errors.append(f"{rid}: UNRESOLVED QUOTE: {e}")
            continue
        if r["category"] == "no_answer" and labels[rid].n:
            errors.append(f"{rid}: no_answer row has labels")
        if r["category"] != "no_answer" and not labels[rid].n:
            errors.append(f"{rid}: answerable row has no labels")
        for e in r["evidence"]:
            if e["grade"] not in (1, 2):
                errors.append(f"{rid}: grade must be 1 or 2")
        filt = (
            Filters(
                exclude_allergens=tuple(f.get("exclude_allergens", [])),
                dietary=tuple(f.get("dietary", [])),
            )
            if not bad
            else Filters()
        )
        for name in r["relevant_menu_item_names"]:
            if name not in by_name:
                errors.append(f"{rid}: unknown menu item {name!r}")
            elif violates(menu[by_name[name]], filt):
                errors.append(f"{rid}: labelled item {name!r} violates the row's own filters")
        for sp, e in zip(labels[rid].spans, r["evidence"], strict=True):
            quote_rows.append(
                (
                    rid,
                    sp.slug,
                    sp.start,
                    sp.end,
                    sp.grade,
                    ev.token_jaccard(r["query"], e["quote"]),
                    ev.token_containment(r["query"], e["quote"]),
                    e["quote"],
                )
            )

    # every quote must be covered (>= 50%) by at least one chunk in EVERY ingested chunk config
    coverage: dict[str, dict] = {}
    async with engine.connect() as c:
        for cfg in cfgs:
            chunks = (
                await c.execute(
                    text(
                        "SELECT d.slug, ch.start_char, ch.end_char FROM chunks ch "
                        "JOIN documents d ON d.id = ch.document_id "
                        "JOIN chunk_configs cc ON cc.id = ch.chunk_config_id WHERE cc.name = :n"
                    ),
                    {"n": cfg},
                )
            ).all()
            uncovered = []
            for rid, slug, s, t, g, *_ in quote_rows:
                span = ev.EvidenceSpan(slug, s, t, g)
                if not any(
                    ev.overlap_fraction(span, ch.slug, ch.start_char, ch.end_char) >= ev.MIN_COVER
                    for ch in chunks
                ):
                    uncovered.append(f"{rid}:{slug}@{s}")
            coverage[cfg] = {"chunks": len(chunks), "uncovered_quotes": uncovered}
            if uncovered:
                errors.append(
                    f"config {cfg}: {len(uncovered)} quote(s) not covered by any chunk: {uncovered[:5]}"
                )

    return {
        "errors": errors,
        "n_rows": len(rows),
        "counts": {
            "category_split": dict(Counter(f"{r['category']}/{r['split']}" for r in rows)),
            "split": dict(Counter(r["split"] for r in rows)),
            "quotes": len(quote_rows),
        },
        "quote_rows": quote_rows,
        "coverage": coverage,
        "labels": labels,
        "docs": docs,
    }


def print_validation(rep: dict, rows: list[dict]) -> None:
    print(
        f"rows: {rep['n_rows']}  quotes: {rep['counts']['quotes']}  splits: {rep['counts']['split']}"
    )
    print("\ncategory x split:")
    for cat in CATEGORIES:
        d = rep["counts"]["category_split"].get(f"{cat}/dev", 0)
        t = rep["counts"]["category_split"].get(f"{cat}/test", 0)
        print(f"  {cat:<20} dev {d:>2}  test {t:>2}  total {d + t:>2}")
    print("\nchunk-config coverage (quotes with no >=50% chunk):")
    for cfg, c in rep["coverage"].items():
        print(f"  {cfg:<22} chunks {c['chunks']:>4}  uncovered {len(c['uncovered_quotes'])}")
    print(
        f"\nquery <-> evidence overlap (flag: jaccard > {JACCARD_FLAG} or containment >= {CONTAIN_FLAG})"
    )
    print(f"  {'id':<8}{'jaccard':>8}{'contain':>9}  flag  quote")
    q_by_id = {r["id"]: r["query"] for r in rows}
    shown = sorted(rep["quote_rows"], key=lambda x: -x[5])
    for rid, slug, _s, _t, _g, j, cont, quote in shown:
        flag = "J" if j > JACCARD_FLAG else ("C" if cont >= CONTAIN_FLAG else "")
        if flag or j >= 0.25:
            print(
                f"  {rid:<8}{j:>8.2f}{cont:>9.2f}  {flag:<4}  [{slug}] {quote[:70]!r}  <- {q_by_id[rid]!r}"
            )
    nj = sum(1 for x in rep["quote_rows"] if x[5] > JACCARD_FLAG)
    nc = sum(1 for x in rep["quote_rows"] if x[5] <= JACCARD_FLAG and x[6] >= CONTAIN_FLAG)
    print(f"\nflagged: jaccard>{JACCARD_FLAG}: {nj}   containment>={CONTAIN_FLAG} only: {nc}")
    print(f"max jaccard: {max((x[5] for x in rep['quote_rows']), default=0):.2f}")
    print(f"\nERRORS: {len(rep['errors'])}")
    for e in rep["errors"]:
        print("  -", e)


def abbreviate(s: str, n: int = 70) -> str:
    s = " ".join(s.split())
    return s if len(s) <= n else s[: n - 1] + "…"


def changes_applied(meta: dict) -> str:
    frozen = (
        f"FROZEN at {meta.get('frozen_at', '?')}, test_sha256 `{meta.get('test_sha256')}`."
        if meta.get("frozen")
        else "Not frozen yet."
    )
    return f"""## Changes applied (dataset v2, after the user review)

{frozen} The hash covers every test row (query, filters, scope, labels, groups, notes); the harness refuses
`--split test` if any of them changes.

1. **Menu-only scope accepted** for all dietary/allergen rows (`scope=["menu"]`). Why: with docs in scope the
   FAQ/practices chunks crowd out the structured items (dev dietary recall 0.036 in the first run). The
   both-scope result is reported as an ablation (`--ignore-row-scope`).
2. **men-14 dropped** ("whats the cheapest thing i can eat"). Price ordering is an aggregation (`ORDER BY price
   LIMIT 1`) for SQL, not semantic retrieval; no embedding can rank by a number it does not read. Kept as a
   finding for the report ("questions RAG should not answer"). 66 rows -> 65 (dev 40 -> 39, test 26 unchanged;
   the other categories' stratification is untouched, menu_lookup dev 9 -> 8).
3. **Label sets completed by query, not by hand** (die-03, die-09, die-12, and die-11 for consistency). Method: take the
   row's own filters (the `Filters` object and `_item_clause` SQL the retriever uses), add the query's
   intent as a category/spice predicate, list every `menu_items` row that passes, then sanity-check the
   list by eye.
   - die-03 "gluten free main": gluten_free AND category in (Mains, Bowls & Curries) -> 4 items, unchanged.
   - die-09 "spicy but no soy": no soy AND spice_level >= 1 AND not a drink -> 7 items (was 4: added Pad Thai,
     Lamb Kofta Plate, Butternut Squash Soup). Ginger Beer (spice 1) is excluded: a drink, not a dish.
   - die-12 "drink dairy and gluten free": no dairy, no gluten, category Drinks -> 4 items, unchanged
     (includes Pinot Grigio, as a diner asking for "a drink" would accept wine).
   - die-11 "egg allergy, what mains": no egg AND category in (Mains, Bowls & Curries) -> 9 items (was 4). Not on
     the approved list: it used a narrower "mains" than die-03/die-07, so it was brought in line (dev row).
4. **die-05 kept as is** ("halal starters, not soup"); a known limitation: embeddings handle negation poorly,
   Phase 5's LLM applies "not soup".
5. **faq-05 reworded** to "do i have to add a tip or is it already on there" (the old query copied 83% of the
   FAQ's words). It now shares almost no words with "gratuity"; that vocabulary gap is the point.
6. **Recall per label group.** Evidence entries take an optional `group` (and a row may set `item_groups`).
   recall@k = groups with at least one member covered in the top-k / groups; no group = its own group.
   nDCG credits a group once (first covering hit, at that hit's grade) and the ideal DCG uses the group's
   MAX grade, so duplicate alternates cannot inflate the ideal. success@k and MRR are unaffected. Grouped:
   alternates that state the same fact, e.g. the dish-guide and PDF description of one dish, the FAQ and PDF
   opening hours, the FAQ and policy dog rule. The menu-item label is NOT merged with prose passages: the
   structured card and a prose chunk are different evidence, and merging them would make the chunk-size
   ablation blind on menu lookups (the item is always retrievable).
"""


def write_review(rep: dict, meta: dict, rows: list[dict]) -> None:
    """Compact review table for the human check before the test split is frozen."""
    qinfo: dict[str, dict] = defaultdict(lambda: {"j": 0.0, "c": 0.0})
    for rid, _slug, _s, _t, _g, j, c, _q in rep["quote_rows"]:
        qinfo[rid]["j"] = max(qinfo[rid]["j"], j)
        qinfo[rid]["c"] = max(qinfo[rid]["c"], c)

    def cell(s: str) -> str:
        return s.replace("|", "\\|")

    lines = [
        "# Retrieval eval set v1: review sheet",
        "",
        f"Status: {'FROZEN' if meta.get('frozen') else 'DRAFT (not frozen)'}. "
        f"{len(rows)} queries. Source of truth: `eval/datasets/retrieval_v1.jsonl`. "
        "This file is generated: `cd api && uv run python ../eval/retrieval/run_retrieval_eval.py --validate --review`.",
        "",
        "## How to review (about 15 minutes)",
        "",
        "For each row check three things:",
        "1. **Phrasing.** Would a real diner type this? Short, casual, typos are fine. It should not echo the document's wording.",
        "2. **Answerable.** Can the corpus really answer it (or, for `no_answer`, genuinely not)? Grep `api/corpus/` when unsure.",
        "3. **Labels complete.** Is there another section (a second doc, the PDF, the dish guide) that also answers it and is missing from the evidence? "
        "Missing labels make good retrieval look bad.",
        "",
        "To change something: edit the row in `eval/datasets/retrieval_v1.jsonl` (query, `relevant_menu_item_names`, or add an `evidence` entry with a "
        "verbatim `quote` from `api/corpus/*.md` or the PDF text, `grade` 2 = directly answers, 1 = partially). "
        "Or just write a note in the **Your notes** column below and tell the agent. "
        "Then run `--validate` (it fails loudly on any quote that does not resolve). Do not edit `split` by hand.",
        "",
        "Flags: **J** = query/evidence token Jaccard > 0.5 (leakage risk); **C** = more than 80% of the query's words appear in an evidence quote; "
        "**?** = labelling judgement call (see Notes).",
        "",
        "| id | cat | split | query | filters | expected items | evidence (grade, doc: quote) | flags / notes | Your notes |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        f = r.get("filters") or {}
        fs = "; ".join(f"{k}={','.join(v)}" for k, v in f.items())
        if set(r.get("scope") or ()) == {"menu"}:
            fs = (fs + "; " if fs else "") + "scope=menu"
        items = ", ".join(r["relevant_menu_item_names"])
        evs = "<br>".join(
            f"({e['grade']}, {e['doc_slug']}{', group ' + e['group'] if e.get('group') else ''}) "
            f"{abbreviate(e['quote'])}"
            for e in r["evidence"]
        )
        flags = []
        info = qinfo.get(r["id"])
        if info and info["j"] > JACCARD_FLAG:
            flags.append(f"**J** {info['j']:.2f}")
        elif info and info["c"] >= CONTAIN_FLAG:
            flags.append(f"**C** {info['c']:.2f}")
        if r.get("notes"):
            flags.append("**?** " + r["notes"])
        if r["category"] == "no_answer":
            flags.append("no answer expected")
        lines.append(
            f"| {r['id']} | {r['category']} | {r['split']} | {cell(r['query'])} | {cell(fs)} | {cell(items)} | "
            f"{cell(evs)} | {cell('; '.join(flags))} | |"
        )
    lines += ["", changes_applied(meta)]
    REVIEW.write_text("\n".join(lines) + "\n")
    print(f"wrote {REVIEW.relative_to(REPO)}")


# --- config parsing -------------------------------------------------------------------------------


def parse_config(spec: str) -> tuple[str, RetrievalConfig]:
    label, _, rest = spec.partition(":")
    kw: dict = {}
    for part in filter(None, rest.split(",")):
        k, _, v = part.partition("=")
        if k == "chunk":
            kw["chunk_config"] = v
        elif k == "mode":
            kw["mode"] = v
        elif k == "rerank":
            kw["rerank"] = v in ("1", "true", "yes")
        elif k == "model":
            kw["embed_model"] = MODEL_ALIASES.get(v, v)
        elif k == "rrf_k":
            kw["rrf_k"] = int(v)
        elif k == "prefix":
            kw["query_prefix"] = None if v == "default" else ("" if v == "none" else v)
        elif k == "fts":
            kw["fts_operator"] = v
        elif k == "index":
            kw["vector_index"] = v
        elif k == "ef":
            kw["ef_search"] = int(v)
        elif k == "rrn":
            kw["rerank_top_n"] = int(v)
        elif k == "cand":
            kw["candidate_k"] = int(v)
        else:
            raise SystemExit(f"unknown config key {k!r} in {spec!r}")
    kw["k"] = max(RETRIEVE_K, kw.get("k", 0))
    return label, RetrievalConfig(**kw)


# --- running --------------------------------------------------------------------------------------


def percentile(values: list[float], p: float) -> float:
    s = sorted(values)
    i = (len(s) - 1) * p
    lo, hi = int(i), min(int(i) + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (i - int(i))


def violating_ids(menu: dict[int, dict], f: Filters) -> set[int]:
    return {i for i, m in menu.items() if violates(m, f)}


async def run_config(
    engine: AsyncEngine,
    label: str,
    cfg: RetrievalConfig,
    rows: list[dict],
    labels: dict[str, ev.Labels],
    menu: dict[int, dict],
    counter: TokenCounter,
    warmup: int,
    repeats: int,
    ignore_scope: bool = False,
    apply_filters: bool = True,
    shared_cache: bool = False,
) -> tuple[list[dict], dict]:
    per_query: list[dict] = []
    offsets: dict[int, tuple[int, int]] = {}
    async with engine.connect() as conn:

        async def fetch_offsets(ids: list[int]) -> None:
            need = [i for i in ids if i not in offsets]
            if need:
                res = await conn.execute(
                    text("SELECT id, start_char, end_char FROM chunks WHERE id = ANY(:ids)"),
                    {"ids": need},
                )
                for r in res:
                    offsets[r.id] = (r.start_char, r.end_char)

        def filters_of(row: dict, applied: bool = False) -> Filters:
            """The row's filters; `applied=True` is what retrieval actually receives (the --no-filters
            ablation retrieves unfiltered but still judges safety against the row's own filters)."""
            f = (row.get("filters") or {}) if (apply_filters or not applied) else {}
            return Filters(
                exclude_allergens=tuple(f.get("exclude_allergens", [])),
                dietary=tuple(f.get("dietary", [])),
            )

        cache_shared = QueryEmbedCache(4096)

        async def once(row: dict) -> RetrievalResult:
            scope = None if ignore_scope else set(row.get("scope") or ("menu", "docs"))
            cache = cache_shared if shared_cache else QueryEmbedCache(8)
            return await retrieve(
                conn, row["query"], filters_of(row, applied=True), cfg, scope, cache=cache
            )

        if shared_cache:  # prime: every timed call below is an embedding-cache HIT
            for row in rows:
                await once(row)

        # latency: untimed warm-up (model load, plan cache), then timed repeats per query.
        # Default: each call uses a fresh embedding cache (cache-MISS timings); --embed-cache hit shares one.
        for row in (rows * max(1, warmup))[:warmup]:
            await once(row)
        samples: dict[str, list[float]] = {s: [] for s in STAGES}
        for row in rows:
            for _ in range(repeats):
                res = await once(row)
                for s in STAGES:
                    samples[s].append(res.timings_ms[s])

        # quality pass (deterministic: DB state + model weights decide the ranking)
        for row in rows:
            res = await once(row)
            await fetch_offsets([h.id for h in res.hits if h.kind == "chunk"])
            ranked = []
            for h in res.hits:
                if h.kind == "menu_item":
                    ranked.append(
                        ev.RankedHit("menu_item", name=h.meta["name"], tokens=counter.count(h.text))
                    )
                else:
                    s, e = offsets[h.id]
                    ranked.append(
                        ev.RankedHit(
                            "chunk",
                            slug=h.meta["slug"],
                            start=s,
                            end=e,
                            tokens=h.meta["token_count"],
                        )
                    )
            lab = labels[row["id"]]
            metrics = ev.query_metrics(ranked, lab)
            f = filters_of(row)
            bad_ids = violating_ids(menu, f) if f.restricts_items else set()
            unsafe = [
                h.meta.get("name") or f"chunk:{h.id}"
                for h in res.hits
                if (h.kind == "menu_item" and h.id in bad_ids)
                or (h.kind == "chunk" and set(h.meta["menu_item_ids"]) & bad_ids)
            ]
            cos = [
                h.scores[k] for h in res.hits for k in ("menu_vec", "chunk_vec") if k in h.scores
            ]
            signals = {
                "final": res.hits[0].score if res.hits else 0.0,
                "cosine": max(cos) if cos else 0.0,
                "rerank": (
                    max(
                        (h.scores["rerank"] for h in res.hits if "rerank" in h.scores), default=None
                    )
                    if cfg.rerank
                    else None
                ),
            }
            per_query.append(
                {
                    "config": label,
                    "id": row["id"],
                    "category": row["category"],
                    "split": row["split"],
                    "query": row["query"],
                    "n_labels": lab.n,
                    "metrics": metrics,
                    "signals": signals,
                    "unsafe_hits": unsafe
                    if f.restricts_items and (f.exclude_allergens or f.dietary)
                    else None,
                    "n_hits": len(res.hits),
                    "timings_ms": res.timings_ms,
                    "hits": [
                        {
                            "rank": i + 1,
                            "kind": h.kind,
                            "id": h.id,
                            "name": h.meta.get("name"),
                            "slug": h.meta.get("slug"),
                            "chunk_index": h.chunk_index,
                            "section": h.meta.get("section_path"),
                            "grade": g,
                            "score": round(h.score, 6),
                            "scores": {k: round(v, 5) for k, v in h.scores.items()},
                            "tokens": rh.tokens,
                        }
                        for i, (h, rh, g) in enumerate(
                            zip(res.hits, ranked, ev.hit_grades(ranked, lab), strict=True)
                        )
                    ],
                }
            )
    latency = {
        s: {"p50": percentile(v, 0.5), "p95": percentile(v, 0.95), "n": len(v)}
        for s, v in samples.items()
        if v and any(x > 0 for x in v)
    }
    return per_query, latency


def summarize(
    label: str,
    cfg: RetrievalConfig,
    per_query: list[dict],
    latency: dict,
    threshold_from: dict | None,
    split: str,
) -> dict:
    out: dict = {
        "config": label,
        "retrieval_config": {k: v for k, v in asdict(cfg).items() if k != "extra"},
        "n_queries": len(per_query),
        "overall": ev.mean_metrics([q["metrics"] for q in per_query]),
        "by_category": {},
        "latency_ms": latency,
    }
    for cat in CATEGORIES:
        qs = [q for q in per_query if q["category"] == cat]
        if qs:
            out["by_category"][cat] = {"n": len(qs), **ev.mean_metrics([q["metrics"] for q in qs])}

    # no-answer: top-1 score distributions and abstention threshold (chosen on dev only)
    ans = [q for q in per_query if q["n_labels"]]
    noa = [q for q in per_query if not q["n_labels"]]
    out["no_answer"] = {"n_answerable": len(ans), "n_no_answer": len(noa), "signals": {}}
    for sig in ("final", "cosine", "rerank"):
        a = [q["signals"][sig] for q in ans if q["signals"][sig] is not None]
        n = [q["signals"][sig] for q in noa if q["signals"][sig] is not None]
        if not a or not n:
            continue
        entry: dict = {"answerable": ev.distribution(a), "no_answer": ev.distribution(n)}
        if split == "dev":
            t = ev.select_threshold(a, n)
            entry["threshold_dev"] = asdict(t) if t else None
            entry["note"] = "threshold chosen and scored on the same dev queries (in-sample)"
        elif threshold_from:
            t0 = threshold_from.get(sig)
            if t0:
                ar, ab = ev.apply_threshold(t0["threshold"], a, n)
                entry["threshold_applied"] = {
                    "threshold": t0["threshold"],
                    "answer_rate": ar,
                    "abstain_rate": ab,
                    "balanced_accuracy": (ar + ab) / 2,
                }
        out["no_answer"]["signals"][sig] = entry

    # filter safety: unsafe items/chunks in top-k for rows that carry allergen/dietary filters
    filt = [q for q in per_query if q["unsafe_hits"] is not None]
    out["filter_safety"] = {
        "n_filtered_queries": len(filt),
        "queries_with_unsafe_hit": sum(1 for q in filt if q["unsafe_hits"]),
        "unsafe_hits_total": sum(len(q["unsafe_hits"]) for q in filt),
        "hits_total": sum(q["n_hits"] for q in filt),
    }
    return out


def rebuild_ablation_summary() -> None:
    rows = []
    for p in sorted(RESULTS.glob("*/summary.json")):
        s = json.loads(p.read_text())
        for c in s["configs"]:
            rows.append((p.parent.name, s["split"], c))
    hdr = (
        "| run | split | config | chunk | mode | rerank | R@1 | R@3 | R@5 | R@10 | succ@5 | MRR | nDCG@5 "
        "| ctxP@5 | R@1024tok | p50 ms | p95 ms |"
    )
    lines = [
        "# Phase 4 retrieval: all runs (raw; curated tables are in ablation_summary.md)",
        "",
        "Every run, raw. Generated by `eval/retrieval/run_retrieval_eval.py` from `eval/results/phase4/*/summary.json`. "
        "Metrics are means over answerable queries; latency is end-to-end `retrieve()` with a cold query-embedding cache.",
        "",
        hdr,
        "|" + "|".join(["---"] * (hdr.count("|") - 1)) + "|",
    ]

    def f(x, nd=3):
        return "-" if x is None else f"{x:.{nd}f}"

    for run, split, c in rows:
        o, rc, lat = c["overall"], c["retrieval_config"], c["latency_ms"].get("total", {})
        lines.append(
            f"| {run} | {split} | {c['config']} | {rc['chunk_config']} | {rc['mode']} | {int(rc['rerank'])} "
            f"| {f(o.get('recall@1'))} | {f(o.get('recall@3'))} | {f(o.get('recall@5'))} | {f(o.get('recall@10'))} "
            f"| {f(o.get('success@5'))} | {f(o.get('mrr'))} | {f(o.get('ndcg@5'))} | {f(o.get('ctx_precision@5'))} "
            f"| {f(o.get('recall@1024tok'))} | {f(lat.get('p50'), 1)} | {f(lat.get('p95'), 1)} |"
        )
    (RESULTS / "all_runs.md").write_text("\n".join(lines) + "\n")


def git_info() -> dict:
    def run(*a: str) -> str:
        return subprocess.run(["git", *a], cwd=REPO, capture_output=True, text=True).stdout.strip()

    return {
        "sha": run("rev-parse", "HEAD"),
        "dirty": bool(run("status", "--porcelain", "--", "api", "eval")),
    }


def versions() -> dict:
    out = {}
    for p in (
        "fastembed",
        "onnxruntime",
        "tokenizers",
        "numpy",
        "SQLAlchemy",
        "asyncpg",
        "pgvector",
    ):
        try:
            out[p] = md.version(p)
        except md.PackageNotFoundError:
            out[p] = None
    return out


async def main_async(args: argparse.Namespace) -> int:
    meta, rows = load_dataset(Path(args.dataset))
    engine = create_async_engine(DB_URL)
    try:
        if args.validate or args.freeze or args.review:
            rep = await validate(engine, meta, rows)
            print_validation(rep, rows)
            if args.review:
                write_review(rep, meta, rows)
            if args.freeze:
                if rep["errors"]:
                    print("refusing to freeze: validation errors")
                    return 1
                meta.update(
                    frozen=True,
                    test_sha256=test_hash(rows),
                    frozen_at=datetime.now(UTC).isoformat(),
                )
                with Path(args.dataset).open("w") as fh:
                    fh.write(json.dumps({"_meta": meta}, ensure_ascii=False) + "\n")
                    for r in rows:
                        fh.write(json.dumps(r, ensure_ascii=False) + "\n")
                print(f"frozen: test_sha256={meta['test_sha256']}")
                return 0
            return 1 if rep["errors"] else 0

        if args.split == "test":
            if not meta.get("frozen"):
                print("refusing: dataset is not frozen (draft). Review, then run with --freeze.")
                return 2
            if meta.get("test_sha256") != test_hash(rows):
                print("refusing: test rows changed after freezing (hash mismatch).")
                return 2
        sel = [r for r in rows if r["split"] == args.split]
        rep = await validate(engine, meta, rows)
        if rep["errors"]:
            print_validation(rep, rows)
            return 1
        labels = rep["labels"]
        menu = await fetch_menu(engine)
        counter = TokenCounter(load_chunk_tokenizer())
        thr_by_label: dict[str, dict] = {}
        if args.threshold_from:
            for c in json.loads(Path(args.threshold_from).read_text())["configs"]:
                thr_by_label[c["config"]] = {
                    sg: v.get("threshold_dev") for sg, v in c["no_answer"]["signals"].items()
                }

        run_id = f"{datetime.now(UTC):%Y%m%dT%H%M%SZ}_{args.split}" + (
            f"_{args.tag}" if args.tag else ""
        )
        out_dir = RESULTS / run_id
        out_dir.mkdir(parents=True)
        configs = [parse_config(c) for c in args.configs]
        summaries, all_pq = [], []
        t0 = time.perf_counter()
        for label, cfg in configs:
            print(
                f"[{label}] {cfg.mode} rerank={cfg.rerank} chunk={cfg.chunk_config} model={cfg.embed_model} ...",
                flush=True,
            )
            pq, lat = await run_config(
                engine,
                label,
                cfg,
                sel,
                labels,
                menu,
                counter,
                args.warmup,
                args.repeats,
                args.ignore_row_scope,
                not args.no_filters,
                args.embed_cache == "hit",
            )
            all_pq += pq
            summaries.append(summarize(label, cfg, pq, lat, thr_by_label.get(label), args.split))
        async with engine.connect() as c:
            pg = (await c.execute(text("SELECT version()"))).scalar_one()
            vec = (
                await c.execute(text("SELECT extversion FROM pg_extension WHERE extname='vector'"))
            ).scalar_one()
            doc_hashes = (
                await c.execute(text("SELECT slug, content_hash FROM documents ORDER BY slug"))
            ).all()
        corpus_hash = hashlib.sha256(
            "".join(f"{s}:{h};" for s, h in doc_hashes).encode()
        ).hexdigest()
        (out_dir / "per_query.jsonl").write_text(
            "".join(json.dumps(q, ensure_ascii=False) + "\n" for q in all_pq)
        )
        (out_dir / "summary.json").write_text(
            json.dumps({"split": args.split, "configs": summaries}, indent=2)
        )
        settings = get_retrieval_settings()
        (out_dir / "run.json").write_text(
            json.dumps(
                {
                    "run_id": run_id,
                    "started_utc": datetime.now(UTC).isoformat(),
                    "elapsed_s": round(time.perf_counter() - t0, 1),
                    "argv": sys.argv,
                    "split": args.split,
                    "n_queries": len(sel),
                    "dataset": {
                        "path": str(Path(args.dataset).relative_to(REPO)),
                        "sha256": hashlib.sha256(Path(args.dataset).read_bytes()).hexdigest(),
                        "frozen": bool(meta.get("frozen")),
                        "test_sha256": meta.get("test_sha256"),
                    },
                    "git": git_info(),
                    "corpus_sha256": corpus_hash,
                    "corpus_docs": {s: h[:12] for s, h in doc_hashes},
                    "models": {
                        "embedders": sorted({c.embed_model for _, c in configs}),
                        "reranker": settings.rerank_model,
                        "onnx_threads": settings.onnx_threads,
                    },
                    "versions": versions(),
                    "postgres": pg.split(",")[0],
                    "pgvector": vec,
                    "host": {
                        "platform": platform.platform(),
                        "python": platform.python_version(),
                        "cpus": os.cpu_count(),
                        "loadavg_at_end": os.getloadavg(),
                    },
                    "row_scope": "ignored (menu+docs)" if args.ignore_row_scope else "per row",
                    "filters": "OFF (ablation)" if args.no_filters else "per row",
                    "latency": {
                        "warmup_calls": args.warmup,
                        "repeats_per_query": args.repeats,
                        "embedding_cache": "shared, primed (hit path)"
                        if args.embed_cache == "hit"
                        else "fresh per call (miss path)",
                    },
                    "configs": {
                        label: {k: v for k, v in asdict(c).items() if k != "extra"}
                        for label, c in configs
                    },
                },
                indent=2,
            )
        )
        rebuild_ablation_summary()
        print(f"\nrun {run_id} -> {out_dir.relative_to(REPO)}")
        for s in summaries:
            o = s["overall"]
            print(
                f"  {s['config']:<10} R@1 {o['recall@1']:.3f} R@3 {o['recall@3']:.3f} R@5 {o['recall@5']:.3f} "
                f"R@10 {o['recall@10']:.3f} succ@5 {o['success@5']:.3f} MRR {o['mrr']:.3f} "
                f"nDCG@5 {o['ndcg@5']:.3f} ctxP@5 {o['ctx_precision@5']:.3f} R@1024tok {o['recall@1024tok']:.3f}"
            )
        return 0
    finally:
        await engine.dispose()


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--dataset", default=str(DATASET))
    ap.add_argument("--split", choices=["dev", "test"], default="dev")
    ap.add_argument("--configs", nargs="+", default=["default"])
    ap.add_argument("--validate", action="store_true", help="check labels only; no retrieval")
    ap.add_argument(
        "--review", action="store_true", help="with --validate: write eval/datasets/REVIEW.md"
    )
    ap.add_argument(
        "--freeze", action="store_true", help="validate, then lock the test split by hash"
    )
    ap.add_argument("--warmup", type=int, default=3, help="untimed retrieval calls before timing")
    ap.add_argument("--repeats", type=int, default=5, help="timed repeats per query (latency)")
    ap.add_argument(
        "--threshold-from",
        help="dev summary.json whose abstention thresholds to apply (matched by config label)",
    )
    ap.add_argument(
        "--ignore-row-scope",
        action="store_true",
        help="search menu+docs for every row (default: honour each row's `scope`)",
    )
    ap.add_argument(
        "--no-filters",
        action="store_true",
        help="retrieve without the row's allergen/dietary filters (safety ablation)",
    )
    ap.add_argument(
        "--embed-cache",
        choices=["miss", "hit"],
        default="miss",
        help="latency: fresh query-embedding cache per call (miss) or a primed shared one (hit)",
    )
    ap.add_argument("--tag", default="", help="suffix for the run id")
    args = ap.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9_-]*", args.tag):
        raise SystemExit("--tag must be [A-Za-z0-9_-]")
    raise SystemExit(asyncio.run(main_async(args)))


if __name__ == "__main__":
    main()
