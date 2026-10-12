# ruff: noqa: E501, ASYNC230, ASYNC240
"""Builds eval/results/phase4/ablation_summary.md from the runs under eval/results/phase4/.

Run: cd api && uv run python ../eval/retrieval/phase4_tables.py
Runs are found by tag (the suffix of the run directory): grid, abl, scope_both, nofilter, nofilter_scope_both,
lat_miss, lat_hit, lat_rerank, test_final (the last one only exists after the frozen test split was run).
Differences between configs are paired-bootstrap 95% intervals over queries (2000 resamples, seed 0): with
35 answerable dev queries a gap of a few points is noise, and the tables say so.
"""

import asyncio
import json
import random
import statistics
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "api"))

from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import create_async_engine  # noqa: E402

from app.retrieval import evaluation as ev  # noqa: E402

RESULTS = REPO / "eval" / "results" / "phase4"
DB_URL = "postgresql+asyncpg://qr:qr@localhost:55432/qr"


def find_run(tag: str) -> Path | None:
    split = "test" if tag.startswith("test") else "dev"
    # exact match on "<timestamp>_<split>_<tag>": "scope_both" must not match "nofilter_scope_both"
    runs = sorted(d for d in RESULTS.glob(f"*_{split}_{tag}") if d.name.split("_", 2)[2] == tag)
    return runs[-1] if runs else None


def load(tag: str):
    d = find_run(tag)
    if d is None:
        return None
    summ = json.loads((d / "summary.json").read_text())
    pq: dict[str, dict[str, dict]] = {}
    for line in (d / "per_query.jsonl").read_text().splitlines():
        r = json.loads(line)
        pq.setdefault(r["config"], {})[r["id"]] = r
    run = json.loads((d / "run.json").read_text())
    return {
        "dir": d,
        "summary": {c["config"]: c for c in summ["configs"]},
        "pq": pq,
        "run": run,
        "split": summ["split"],
    }


def f3(x):
    return "-" if x is None else f"{x:.3f}"


def f2(x):
    return "-" if x is None else f"{x:.2f}"


def boot_diff(a: dict[str, dict], b: dict[str, dict], metric: str, n=2000, seed=0):
    """Paired bootstrap of mean(b) - mean(a) over queries where both define the metric."""
    ids = [
        i
        for i in a
        if a[i]["metrics"].get(metric) is not None and b[i]["metrics"].get(metric) is not None
    ]
    d = [b[i]["metrics"][metric] - a[i]["metrics"][metric] for i in ids]
    rng = random.Random(seed)
    means = sorted(statistics.fmean(rng.choices(d, k=len(d))) for _ in range(n))
    return statistics.fmean(d), means[int(0.025 * n)], means[int(0.975 * n)], len(d)


def sig(lo, hi):
    return "" if lo <= 0 <= hi else " *"


def loo_ba(ans: list[float], noa: list[float]):
    """Leave-one-out balanced accuracy of the threshold rule (threshold re-chosen without the held-out query)."""
    ok_a = ok_n = 0
    for i in range(len(ans)):
        t = ev.select_threshold(ans[:i] + ans[i + 1 :], noa)
        ok_a += bool(t and ans[i] >= t.threshold)
    for i in range(len(noa)):
        t = ev.select_threshold(ans, noa[:i] + noa[i + 1 :])
        ok_n += bool(t and noa[i] < t.threshold)
    return (ok_a / len(ans) + ok_n / len(noa)) / 2, ok_a / len(ans), ok_n / len(noa)


async def chunk_stats() -> dict[str, tuple[int, float]]:
    e = create_async_engine(DB_URL)
    async with e.connect() as c:
        rows = await c.execute(
            text(
                "SELECT cc.name, count(*) n, avg(ch.token_count) AS avg_tok FROM chunks ch "
                "JOIN chunk_configs cc ON cc.id = ch.chunk_config_id GROUP BY 1"
            )
        )
        out = {r.name: (r.n, float(r.avg_tok)) for r in rows}
    await e.dispose()
    return out


def section_grid(L, grid, stats):
    L += [
        "## 1. Chunking grid (dev, 35 answerable queries, bge-small, no rerank)",
        "",
        "13 chunk configs x 3 modes. R@5 = label-group recall, nDCG@5 graded, B = recall within a 1,024-token context budget. "
        f"Source: `{grid['dir'].relative_to(REPO)}`.",
        "",
        "| chunk config | chunks | tokens/chunk | vec R@5 | vec nDCG@5 | vec B | fts R@5 | fts nDCG@5 | hyb R@5 | hyb nDCG@5 | hyb B |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    names = []
    for lab in grid["summary"]:
        n = lab.rsplit(".", 1)[0]
        if n not in names:
            names.append(n)
    ranking = []
    for n in names:
        v, f, h = (grid["summary"][f"{n}.{m}"]["overall"] for m in ("vector", "fts", "hybrid"))
        cnt, tok = stats.get(n, (0, 0.0))
        L.append(
            f"| {n} | {cnt} | {tok:.0f} | {f3(v['recall@5'])} | {f3(v['ndcg@5'])} | {f3(v['recall@1024tok'])} | {f3(f['recall@5'])} | {f3(f['ndcg@5'])} "
            f"| {f3(h['recall@5'])} | {f3(h['ndcg@5'])} | {f3(h['recall@1024tok'])} |"
        )
        for m, o in (("vector", v), ("fts", f), ("hybrid", h)):
            ranking.append((o["ndcg@5"], o["recall@1024tok"], n, m))
    ranking.sort(reverse=True)
    L += ["", "Top 6 (config, mode) by nDCG@5, ties broken by budget recall:", ""]
    L += [
        f"{i + 1}. {n} / {m}: nDCG@5 {a:.3f}, budget recall {b:.3f}"
        for i, (a, b, n, m) in enumerate(ranking[:6])
    ]
    L.append("")


ABL_ROWS = [
    # (label, baseline label, description)
    ("{P}.vec", None, "vector only"),
    ("{P}.fts_or", None, "FTS only, OR query (default)"),
    ("{P}.fts_and", "{P}.fts_or", "FTS only, AND query"),
    ("{P}.hyb", None, "hybrid RRF k=60 (default)"),
    ("{P}.hyb_and", "{P}.hyb", "hybrid, FTS AND"),
    ("{P}.hyb_rrf10", "{P}.hyb", "hybrid, RRF k=10"),
    ("{P}.hyb_rrf30", "{P}.hyb", "hybrid, RRF k=30"),
    ("{P}.hyb_rrf100", "{P}.hyb", "hybrid, RRF k=100"),
    ("{P}.hyb_nopfx", "{P}.hyb", "hybrid, no bge query prefix"),
    ("{P}.vec_nopfx", "{P}.vec", "vector, no bge query prefix"),
    ("{P}.hyb_arctic", "{P}.hyb", "hybrid, arctic-xs instead of bge"),
    ("{P}.vec_arctic", "{P}.vec", "vector, arctic-xs instead of bge"),
    ("{P}.hyb_head", "{P}.hyb", "hybrid, headings prepended to the embedded text"),
    ("{P}.vec_head", "{P}.vec", "vector, headings prepended"),
    ("{P}.rr10", "{P}.hyb", "hybrid + rerank top 10"),
    ("{P}.rr20", "{P}.hyb", "hybrid + rerank top 20"),
    ("{P}.vec_rr20", "{P}.vec", "vector + rerank top 20"),
]


def section_ablation(L, abl):
    L += [
        "## 2. Ablations on the best two chunk configs (dev)",
        "",
        "P = `A` is fixed_128_o30, `B` is recursive_256_o15 (the Phase 4 default before this study). "
        "dnDCG@5 / dR@5 are paired-bootstrap mean differences vs the baseline row named in the last column, with 95% intervals; "
        "`*` marks an interval that excludes 0. 35 answerable queries: most gaps are inside the noise.",
        "",
        f"Source: `{abl['dir'].relative_to(REPO)}`.",
        "",
    ]
    for P, name in (("A", "fixed_128_o30"), ("B", "recursive_256_o15")):
        L += [
            f"### {P} = {name}",
            "",
            "| variant | R@1 | R@5 | R@10 | MRR | nDCG@5 | B (1024 tok) | dnDCG@5 vs base [95% CI] | dR@5 vs base [95% CI] | base |",
            "|---|---|---|---|---|---|---|---|---|---|",
        ]
        for lab, base, desc in ABL_ROWS:
            lab, base = lab.format(P=P), base.format(P=P) if base else None
            s = abl["summary"].get(lab)
            if not s:
                continue
            o = s["overall"]
            cells = ["", "", ""]
            if base:
                dn = boot_diff(abl["pq"][base], abl["pq"][lab], "ndcg@5")
                dr = boot_diff(abl["pq"][base], abl["pq"][lab], "recall@5")
                cells = [
                    f"{dn[0]:+.3f} [{dn[1]:+.3f}, {dn[2]:+.3f}]{sig(dn[1], dn[2])}",
                    f"{dr[0]:+.3f} [{dr[1]:+.3f}, {dr[2]:+.3f}]{sig(dr[1], dr[2])}",
                    base.split(".")[1],
                ]
            L.append(
                f"| {desc} | {f3(o['recall@1'])} | {f3(o['recall@5'])} | {f3(o['recall@10'])} | {f3(o['mrr'])} | {f3(o['ndcg@5'])} | {f3(o['recall@1024tok'])} | {cells[0]} | {cells[1]} | {cells[2]} |"
            )
        L.append("")
    # mode by category
    L += ["### Mode by category (nDCG@5, answerable queries)", ""]
    cats = [
        "menu_lookup",
        "dietary_allergen",
        "faq_policy",
        "kitchen_practices",
        "pdf_only",
        "comparison",
    ]
    L += ["| config | " + " | ".join(cats) + " |", "|---|" + "---|" * len(cats)]
    for lab in (
        "A.vec",
        "A.fts_or",
        "A.hyb",
        "A.hyb_and",
        "B.vec",
        "B.fts_or",
        "B.hyb",
        "B.hyb_and",
    ):
        bc = abl["summary"][lab]["by_category"]
        n_by = {c: bc.get(c, {}).get("n", 0) for c in cats}
        L.append(
            f"| {lab} | "
            + " | ".join(f"{f3(bc.get(c, {}).get('ndcg@5'))} (n={n_by[c]})" for c in cats)
            + " |"
        )
    L.append("")


def section_filters(L, abl, scope_both, nofilter, nofilter_both):
    L += [
        "## 3. Scope and filters (dev)",
        "",
        "Dietary/allergen rows carry `scope=menu` (user decision). `--ignore-row-scope` searches menu+docs for them as well. "
        "`--no-filters` retrieves without the row's allergen/dietary filters but still judges safety against them. "
        "Dietary rows on dev: 7.",
        "",
        "| config | scope | filters | dietary R@5 | dietary nDCG@5 | dietary succ@5 | queries with unsafe hit | unsafe hits / hits |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for lab in ("A.hyb", "B.hyb", "A.vec", "B.vec"):
        for scope, flt, run in (
            ("menu (row)", "on", abl),
            ("menu (row)", "OFF", nofilter),
            ("menu+docs", "on", scope_both),
            ("menu+docs", "OFF", nofilter_both),
        ):
            s = run["summary"][lab]
            d = s["by_category"]["dietary_allergen"]
            fs = s["filter_safety"]
            L.append(
                f"| {lab} | {scope} | {flt} | {f3(d['recall@5'])} | {f3(d['ndcg@5'])} | {f3(d['success@5'])} | {fs['queries_with_unsafe_hit']} / {fs['n_filtered_queries']} | {fs['unsafe_hits_total']} / {fs['hits_total']} |"
            )
    L += ["", "Overall (all dev queries) effect of searching docs for the dietary rows:", ""]
    L += [
        "| config | overall R@5 menu-only | overall R@5 menu+docs | overall nDCG@5 menu-only | menu+docs |",
        "|---|---|---|---|---|",
    ]
    for lab in ("A.hyb", "B.hyb", "A.vec", "B.vec"):
        a, b = abl["summary"][lab]["overall"], scope_both["summary"][lab]["overall"]
        L.append(
            f"| {lab} | {f3(a['recall@5'])} | {f3(b['recall@5'])} | {f3(a['ndcg@5'])} | {f3(b['ndcg@5'])} |"
        )
    L += [
        "",
        "The stress test (12 allergens + 5 diets x 3 templates, both chunk configs) is in `filter_study.md`.",
        "",
    ]


def section_decision(L, abl):
    pairs = [
        ("B.hyb", "A.hyb", "chunk size alone: recursive_256_o15 -> fixed_128_o30 (hybrid OR)"),
        ("B.hyb", "A.hyb_and", "old default -> candidate default (fixed_128_o30, hybrid, FTS AND)"),
        ("A.hyb", "A.hyb_and", "candidate: FTS OR -> AND, same chunks"),
        ("A.vec", "A.hyb_and", "candidate vs vector-only, same chunks"),
        ("B.vec", "B.hyb_and", "recursive_256_o15: vector-only -> hybrid AND"),
    ]
    L += [
        "## 2b. Choosing the production default (paired bootstrap, dev)",
        "",
        "| change | dnDCG@5 [95% CI] | dR@5 [95% CI] | dMRR [95% CI] | dBudget recall [95% CI] |",
        "|---|---|---|---|---|",
    ]
    for a, b, desc in pairs:
        cells = []
        for m in ("ndcg@5", "recall@5", "mrr", "recall@1024tok"):
            d = boot_diff(abl["pq"][a], abl["pq"][b], m)
            cells.append(f"{d[0]:+.3f} [{d[1]:+.3f}, {d[2]:+.3f}]{sig(d[1], d[2])}")
        L.append(f"| {desc} | " + " | ".join(cells) + " |")
    L.append("")


def section_abstain(L, abl):
    L += [
        "## 4. Abstention signal (dev)",
        "",
        "Rule: answer when the signal >= threshold, otherwise say 'I don't know'. Signal = top-1 RRF score (`final`, hybrid), best raw cosine among the returned hits (`cosine`), "
        "or the cross-encoder score of the best hit (`rerank`, rerank run). Threshold chosen to maximise balanced accuracy (BA) on the dev queries; "
        "`in-sample` = scored on the queries it was chosen on, `LOO` = leave-one-out (threshold re-chosen without the held-out query). "
        "Dev has 35 answerable and only **4 no-answer** queries: one query is 25% of the abstain rate, so read the BA as indicative, not precise.",
        "",
        "| config | signal | answerable median | no-answer median | no-answer max | threshold | answered (answerable) | abstained (no-answer) | BA in-sample | BA LOO |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for lab in (
        "B.hyb",
        "A.hyb",
        "A.hyb_and",
        "B.vec",
        "A.vec",
        "B.rr20",
        "A.rr20",
        "prod_rr3",
        "prod_rr5",
        "final:prod_rr10",
    ):
        pqs = abl["pq"].get(lab)
        if not pqs:
            continue
        for s in ("final", "cosine", "rerank"):
            ans = [
                q["signals"][s]
                for q in pqs.values()
                if q["n_labels"] and q["signals"][s] is not None
            ]
            noa = [
                q["signals"][s]
                for q in pqs.values()
                if not q["n_labels"] and q["signals"][s] is not None
            ]
            if not ans or not noa:
                continue
            t = ev.select_threshold(ans, noa)
            loo = loo_ba(ans, noa)
            L.append(
                f"| {lab} | {s} | {statistics.median(ans):.4f} | {statistics.median(noa):.4f} | {max(noa):.4f} | {t.threshold:.4f} | {t.answer_rate:.1%} | {t.abstain_rate:.1%} | {t.balanced_accuracy:.3f} | {loo[0]:.3f} |"
            )
    L.append("")


def section_latency(L, miss, hit, rr, extra=()):
    L += [
        "## 5. Latency per stage (ms, in-process `retrieve()`, warm: 3 warm-up calls + 5 repeats x 39 dev queries = 195 samples)",
        "",
        "Stages: embed = query embedding (ONNX, in a thread), vector / fts = SQL, fuse = RRF in Python, rerank = cross-encoder, total = whole call. "
        "Host load average during these runs was 4.2 to 6.0 on 10 CPUs (`uptime`, a busy laptop, not a quiet machine). Source runs: "
        f"`{miss['dir'].name}`, `{hit['dir'].name}`, `{rr['dir'].name}`.",
        "",
        "| config | cache | embed p50 | embed p95 | vector p50 | vector p95 | fts p50 | fts p95 | fuse p50 | rerank p50 | rerank p95 | total p50 | total p95 |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]

    def row(run, lab, cache):
        lat = run["summary"][lab]["latency_ms"]

        def g(stage, p):
            return f2(lat.get(stage, {}).get(p))

        L.append(
            f"| {lab} | {cache} | {g('embed', 'p50')} | {g('embed', 'p95')} | {g('vector', 'p50')} | {g('vector', 'p95')} | {g('fts', 'p50')} | {g('fts', 'p95')} | {g('fuse', 'p50')} | {g('rerank', 'p50')} | {g('rerank', 'p95')} | {g('total', 'p50')} | {g('total', 'p95')} |"
        )

    for lab in ("A.vec", "A.fts", "A.hyb", "A.hyb_and", "B.vec", "B.hyb", "B.hyb_and"):
        row(miss, lab, "miss")
    for lab in ("A.vec", "A.hyb", "B.vec", "B.hyb"):
        row(hit, lab, "hit")
    for lab in ("A.rr10", "A.rr20", "B.rr20"):
        row(rr, lab, "miss")
    for run, lab in extra:
        row(run, lab, "miss")
    L.append("")


def section_test(L, test, fdev=None):
    if not test:
        return
    L += [
        "## 6. Frozen test split (26 queries, run once)",
        "",
        f"Source: `{test['dir'].relative_to(REPO)}`; dataset hash {test['run']['dataset']['test_sha256']}.",
        "",
        "| config | R@1 | R@3 | R@5 | R@10 | succ@5 | MRR | nDCG@5 | ctxP@5 | B (1024 tok) | p50 ms | p95 ms |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for lab, s in test["summary"].items():
        o = s["overall"]
        lat = s["latency_ms"].get("total", {})
        L.append(
            f"| {lab} | {f3(o['recall@1'])} | {f3(o['recall@3'])} | {f3(o['recall@5'])} | {f3(o['recall@10'])} | {f3(o['success@5'])} | {f3(o['mrr'])} | {f3(o['ndcg@5'])} | {f3(o['ctx_precision@5'])} | {f3(o['recall@1024tok'])} | {f2(lat.get('p50'))} | {f2(lat.get('p95'))} |"
        )
    if fdev:
        L += ["", f"Same configs on dev (`{fdev['dir'].name}`), for comparison:", ""]
        L += [
            "| config | R@5 | succ@5 | MRR | nDCG@5 | B (1024 tok) |",
            "|---|---|---|---|---|---|",
        ]
        for lab, s in fdev["summary"].items():
            o = s["overall"]
            L.append(
                f"| {lab} | {f3(o['recall@5'])} | {f3(o['success@5'])} | {f3(o['mrr'])} | {f3(o['ndcg@5'])} | {f3(o['recall@1024tok'])} |"
            )
    L += ["", "By category (nDCG@5, n):", ""]
    cats = [
        "menu_lookup",
        "dietary_allergen",
        "faq_policy",
        "kitchen_practices",
        "pdf_only",
        "comparison",
    ]
    L += ["| config | " + " | ".join(cats) + " |", "|---|" + "---|" * len(cats)]
    for lab, s in test["summary"].items():
        bc = s["by_category"]
        L.append(
            f"| {lab} | "
            + " | ".join(
                f"{f3(bc.get(c, {}).get('ndcg@5'))} ({bc.get(c, {}).get('n', 0)})" for c in cats
            )
            + " |"
        )
    L += ["", "Abstention threshold chosen on dev, applied to test:", ""]
    L += [
        "| config | signal | threshold (dev) | answered (answerable) | abstained (no-answer) | BA |",
        "|---|---|---|---|---|---|",
    ]
    for lab, s in test["summary"].items():
        for sg, e in s["no_answer"]["signals"].items():
            ta = e.get("threshold_applied")
            if ta:
                L.append(
                    f"| {lab} | {sg} | {ta['threshold']:.4f} | {ta['answer_rate']:.1%} | {ta['abstain_rate']:.1%} | {ta['balanced_accuracy']:.3f} |"
                )
    ids = list(next(iter(test["pq"].values())))
    labs = list(test["pq"])
    if len(labs) >= 2:
        L += ["", "Paired bootstrap on test (nDCG@5, vs the first config):", ""]
        for lab in labs[1:]:
            d = boot_diff(test["pq"][labs[0]], test["pq"][lab], "ndcg@5")
            L.append(
                f"- {lab} - {labs[0]}: {d[0]:+.3f} [{d[1]:+.3f}, {d[2]:+.3f}]{sig(d[1], d[2])} over {d[3]} answerable queries"
            )
    del ids
    L.append("")


def main() -> None:
    grid, abl = load("grid"), load("abl")
    scope_both, nofilter, nofilter_both = (
        load("scope_both"),
        load("nofilter"),
        load("nofilter_scope_both"),
    )
    miss, hit, rr = load("lat_miss"), load("lat_hit"), load("lat_rerank")
    test, fdev = load("test_final"), load("final_dev")
    stats = asyncio.run(chunk_stats())
    L = [
        "# Phase 4 ablation summary (dev split unless stated)",
        "",
        "Generated by `eval/retrieval/phase4_tables.py` from the run directories next to this file. `all_runs.md` has every run in raw form. "
        "Dataset: `eval/datasets/retrieval_v1.jsonl` v2, 39 dev / 26 test queries, test split frozen (see `eval/datasets/REVIEW.md`).",
        "",
    ]
    section_grid(L, grid, stats)
    section_ablation(L, abl)
    section_decision(L, abl)
    section_filters(L, abl, scope_both, nofilter, nofilter_both)
    gate = load("gate")
    pq_all = {**abl["pq"], **gate["pq"], **{f"final:{k}": v for k, v in fdev["pq"].items()}}
    section_abstain(L, {"pq": pq_all})
    section_latency(
        L,
        miss,
        hit,
        rr,
        [(fdev, "prod"), (fdev, "prod_rr10"), (gate, "prod_rr3"), (gate, "prod_rr5")],
    )
    section_test(L, test, fdev)
    L += [
        "## Other result files",
        "",
        "- `filter_study.md`: unsafe-item rate off/on, chunk over-linking cost.",
        "- `../../../bench/results/phase4-index.md`: HNSW recall and latency vs exact, by scenario and setting.",
    ]
    (RESULTS / "ablation_summary.md").write_text("\n".join(L) + "\n")
    print("wrote", (RESULTS / "ablation_summary.md").relative_to(REPO))


if __name__ == "__main__":
    main()
