# ruff: noqa: E501, ASYNC230, ASYNC240
"""Metadata-filter study (Phase 4, WP6). Two questions, no labels from the test split are used.

  1. SAFETY: with allergen/dietary filters OFF, how often does the top-10 contain an item (or a chunk about an item)
     that violates the filter? With filters ON it must be 0.  Stress set: every allergen x 3 neutral query
     templates (+ every dietary tag x 3), modes vector and hybrid, two chunk configs.
  2. COST of chunk over-linking: a chunk is dropped when ANY menu item it mentions violates the filter. Big chunks
     mention several dishes, so they are dropped for allergens that only one of the dishes has.
     Measured per chunk config: share of chunks dropped, share of dropped chunks that also mention a safe dish
     ("mixed"), and the share of DEV evidence quotes that are themselves safe but lose every covering chunk
     ("collateral label loss").

Run: cd api && uv run python ../eval/retrieval/filter_study.py
Output: eval/results/phase4/filter_study.{json,md}
"""

import asyncio
import json
import os
import statistics
import sys
from datetime import UTC, datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "api"))
sys.path.insert(0, str(REPO / "eval" / "retrieval"))

import run_retrieval_eval as rre  # noqa: E402
from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import create_async_engine  # noqa: E402

from app.retrieval import evaluation as ev  # noqa: E402
from app.retrieval.cache import QueryEmbedCache  # noqa: E402
from app.retrieval.config import Filters, RetrievalConfig  # noqa: E402
from app.retrieval.ingest import build_linker, load_menu  # noqa: E402
from app.retrieval.parse import load_corpus  # noqa: E402
from app.retrieval.search import retrieve  # noqa: E402
from app.retrieval.settings import get_retrieval_settings  # noqa: E402
from app.vocab import ALLERGENS, DIETARY_TAGS  # noqa: E402

OUT = REPO / "eval" / "results" / "phase4"
CHUNK_CONFIGS = ("fixed_128_o30", "recursive_256_o15")
TEMPLATES = ("{x} dishes", "what has {x} in it", "recommend something tasty")


def pretty(x: str) -> str:
    return x.replace("_", " ")


async def safety(engine, menu: dict[int, dict]) -> dict:
    cases: list[tuple[str, Filters, str]] = []
    for a in ALLERGENS:
        for t in TEMPLATES:
            cases.append((t.format(x=pretty(a)), Filters(exclude_allergens=(a,)), f"allergen:{a}"))
    for d in DIETARY_TAGS:
        for t in TEMPLATES:
            cases.append((t.format(x=pretty(d)), Filters(dietary=(d,)), f"dietary:{d}"))
    out = {}
    async with engine.connect() as conn:
        for chunk in CHUNK_CONFIGS:
            for mode in ("vector", "hybrid"):
                cfg = RetrievalConfig(chunk_config=chunk, mode=mode, k=10)  # type: ignore[arg-type]
                for filtered in (False, True):
                    n_q = n_bad_q = n_hits = n_bad_hits = 0
                    for query, f, _tag in cases:
                        res = await retrieve(
                            conn,
                            query,
                            f if filtered else Filters(),
                            cfg,
                            None,
                            cache=QueryEmbedCache(8),
                        )
                        bad = rre.violating_ids(menu, f)
                        nb = sum(
                            1
                            for h in res.hits
                            if (h.kind == "menu_item" and h.id in bad)
                            or (h.kind == "chunk" and set(h.meta["menu_item_ids"]) & bad)
                        )
                        n_q += 1
                        n_hits += len(res.hits)
                        n_bad_hits += nb
                        n_bad_q += nb > 0
                    out[f"{chunk}|{mode}|filters_{'on' if filtered else 'off'}"] = {
                        "queries": n_q,
                        "queries_with_unsafe_hit": n_bad_q,
                        "hits": n_hits,
                        "unsafe_hits": n_bad_hits,
                        "unsafe_hit_rate": round(n_bad_hits / n_hits, 4) if n_hits else 0.0,
                    }
    return out


async def overlink(engine, menu: dict[int, dict]) -> dict:
    docs = {d.slug: d for d in load_corpus(get_retrieval_settings().corpus_dir)}
    _meta, rows = rre.load_dataset()
    quotes = []  # dev answerable evidence quotes: (slug, start, end)
    for r in rows:
        if r["split"] != "dev":
            continue
        for e in r["evidence"]:
            s, t = ev.resolve_quote(docs[e["doc_slug"]].content, e["quote"])
            quotes.append((e["doc_slug"], s, t, e["quote"]))
    async with engine.connect() as conn:
        mrows = await load_menu(conn)
        link = build_linker(mrows)
        quote_links = {(s, a, b): set(link(q)) for s, a, b, q in quotes}
        configs = [
            r.name for r in await conn.execute(text("SELECT name FROM chunk_configs ORDER BY name"))
        ]
        res = {}
        for cfg in configs:
            chunks = (
                await conn.execute(
                    text(
                        "SELECT d.slug, ch.start_char, ch.end_char, ch.menu_item_ids, ch.token_count "
                        "FROM chunks ch JOIN documents d ON d.id = ch.document_id "
                        "JOIN chunk_configs cc ON cc.id = ch.chunk_config_id WHERE cc.name = :n"
                    ),
                    {"n": cfg},
                )
            ).all()
            per_allergen = {}
            for a in ALLERGENS:
                f = Filters(exclude_allergens=(a,), available_only=False)
                bad = rre.violating_ids(menu, f)
                if not bad:
                    continue
                dropped = [c for c in chunks if set(c.menu_item_ids) & bad]
                mixed = [c for c in dropped if set(c.menu_item_ids) - bad]
                safe_q = [q for q in quotes if not (quote_links[(q[0], q[1], q[2])] & bad)]
                lost = 0
                for slug, s, t, _q in safe_q:
                    span = ev.EvidenceSpan(slug, s, t)
                    cover = [
                        c
                        for c in chunks
                        if ev.overlap_fraction(span, c.slug, c.start_char, c.end_char)
                        >= ev.MIN_COVER
                    ]
                    if cover and all(set(c.menu_item_ids) & bad for c in cover):
                        lost += 1
                per_allergen[a] = {
                    "dropped": len(dropped),
                    "dropped_mixed": len(mixed),
                    "safe_quotes": len(safe_q),
                    "safe_quotes_lost": lost,
                }
            n_chunks = len(chunks)
            res[cfg] = {
                "chunks": n_chunks,
                "mean_tokens": round(statistics.fmean(c.token_count for c in chunks), 1),
                "linked_share": round(sum(1 for c in chunks if c.menu_item_ids) / n_chunks, 3),
                "mean_linked_items": round(
                    statistics.fmean(len(c.menu_item_ids) for c in chunks), 2
                ),
                "mean_dropped_share": round(
                    statistics.fmean(v["dropped"] / n_chunks for v in per_allergen.values()), 4
                ),
                "mean_mixed_share_of_dropped": round(
                    statistics.fmean(
                        v["dropped_mixed"] / v["dropped"]
                        for v in per_allergen.values()
                        if v["dropped"]
                    ),
                    3,
                ),
                "collateral_label_loss_mean": round(
                    statistics.fmean(
                        v["safe_quotes_lost"] / v["safe_quotes"]
                        for v in per_allergen.values()
                        if v["safe_quotes"]
                    ),
                    4,
                ),
                "collateral_label_loss_max": round(
                    max(
                        v["safe_quotes_lost"] / v["safe_quotes"]
                        for v in per_allergen.values()
                        if v["safe_quotes"]
                    ),
                    4,
                ),
                "n_dev_quotes": len(quotes),
                "per_allergen": per_allergen,
            }
    return res


def render(o: dict) -> str:
    n_all = len(next(iter(o["overlink"].values()))["per_allergen"])
    L = [
        "# Phase 4: metadata-filter study",
        "",
        f"Generated {o['generated_utc']} by `eval/retrieval/filter_study.py`. No test-split labels used.",
        "",
        "## 1. Unsafe items in the top-10: filters off vs on",
        "",
        f"Stress set: {len(ALLERGENS)} allergens x {len(TEMPLATES)} neutral templates + {len(DIETARY_TAGS)} dietary tags x {len(TEMPLATES)}, "
        "scope menu+docs, k=10. 'Unsafe' = a menu item that violates the filter, or a chunk that mentions such an item.",
        "",
        "| chunk config | mode | filters | queries | queries with an unsafe hit | unsafe hits / hits | rate |",
        "|---|---|---|---|---|---|---|",
    ]
    for key, v in o["safety"].items():
        c, m, f = key.split("|")
        L.append(
            f"| {c} | {m} | {f.replace('filters_', '')} | {v['queries']} | {v['queries_with_unsafe_hit']} "
            f"| {v['unsafe_hits']} / {v['hits']} | {v['unsafe_hit_rate']:.1%} |"
        )
    L += [
        "",
        f"## 2. Cost of chunk over-linking (one allergen filter at a time, mean over the {n_all} allergens that appear on the menu)",
        "",
        "`dropped` = share of the config's chunks removed by the filter; `mixed` = share of dropped chunks that ALSO mention a safe dish; "
        "`collateral loss` = share of dev evidence quotes that are themselves safe but lose every covering chunk.",
        "",
        "| chunk config | chunks | mean tokens | chunks linked to a dish | mean dishes per chunk | dropped | mixed (of dropped) | collateral loss mean | max |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for cfg, v in o["overlink"].items():
        L.append(
            f"| {cfg} | {v['chunks']} | {v['mean_tokens']} | {v['linked_share']:.1%} | {v['mean_linked_items']} "
            f"| {v['mean_dropped_share']:.1%} | {v['mean_mixed_share_of_dropped']:.1%} | {v['collateral_label_loss_mean']:.1%} | {v['collateral_label_loss_max']:.1%} |"
        )
    return "\n".join(L) + "\n"


async def main() -> None:
    engine = create_async_engine(rre.DB_URL)
    try:
        menu = await rre.fetch_menu(engine)
        o = {
            "generated_utc": datetime.now(UTC).isoformat(),
            "host_load": os.getloadavg(),
            "safety": await safety(engine, menu),
            "overlink": await overlink(engine, menu),
        }
    finally:
        await engine.dispose()
    (OUT / "filter_study.json").write_text(json.dumps(o, indent=2))
    (OUT / "filter_study.md").write_text(render(o))
    print(render(o))


if __name__ == "__main__":
    asyncio.run(main())
