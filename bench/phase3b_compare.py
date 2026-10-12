# ruff: noqa: E501
"""Before/after table for the workers + pool-sizing follow-up.

Before = rows of bench/results/phase3-repeats.md (single uvicorn worker, pre_ping pool).
After  = bench/results/phase3b-*-{A,C}.json written by bench/e2e_latency.py --label b...
Run: python3 bench/phase3b_compare.py     Writes bench/results/phase3b-repeats.md. Pure stdlib.
"""

import json
import re
import statistics
from pathlib import Path

RES = Path(__file__).resolve().parent / "results"
AFTER = {  # path label -> (path kind, run name)
    "host": ["b", "b-rep1", "b-rep2"],
    "in-network": ["b-docker", "b-docker-rep1", "b-docker-rep2", "b-docker-cgroup"],
}


def before_rows() -> dict[tuple[str, str], list[dict]]:
    rows: dict[tuple[str, str], list[dict]] = {}
    pat = re.compile(
        r"^\| (host|in-network): (A sequential|C \d+/s) \| (\S+) \| (\d+) \| (\d+) \| ([\d.]+) \| ([\d.]+) \| ([\d.]+) \| ([\d.]+) \|$"
    )
    for line in (RES / "phase3-repeats.md").read_text().splitlines():
        m = pat.match(line)
        if m:
            path, scen, run, n, err, p50, p95, p99, mx = m.groups()
            rows.setdefault((path, scen), []).append(
                dict(
                    run=run,
                    n=int(n),
                    err=int(err),
                    p50=float(p50),
                    p95=float(p95),
                    p99=float(p99),
                    max=float(mx),
                )
            )
    return rows


def after_rows() -> dict[tuple[str, str], list[dict]]:
    rows: dict[tuple[str, str], list[dict]] = {}
    for path, labels in AFTER.items():
        for lab in labels:
            a = RES / f"phase3{lab}-A.json"
            c = RES / f"phase3{lab}-C.json"
            if a.exists():
                s = json.loads(a.read_text())["summary"]["e2e_last_ms"]
                rows.setdefault((path, "A sequential"), []).append(_row(lab, s))
            if c.exists():
                for rate, r in json.loads(c.read_text()).items():
                    s = r["summary"].get("e2e_last_ms")
                    if s:
                        rows.setdefault((path, f"C {rate}/s"), []).append(_row(lab, s))
    return rows


def _row(lab: str, s: dict) -> dict:
    return dict(
        run=lab,
        n=s["count"],
        err=s["errors"],
        p50=s["p50"],
        p95=s["p95"],
        p99=s["p99"],
        max=s["max"],
    )


def f(x: float) -> str:
    return f"{x:.1f}" if x >= 100 else f"{x:.2f}"


def summ(rs: list[dict]) -> str:
    if not rs:
        return "-"
    p99 = [r["p99"] for r in rs]
    ok = sum(1 for v in p99 if v < 100)
    return (
        f"{len(rs)} | {ok}/{len(rs)} | {f(statistics.median(r['p50'] for r in rs))} | "
        f"{f(statistics.median(r['p95'] for r in rs))} | {f(statistics.median(p99))} | {f(max(p99))} | "
        + ", ".join(f(v) for v in p99)
    )


def main() -> None:
    b, a = before_rows(), after_rows()
    out = [
        "# Phase 3b: before/after (1 worker, pre_ping) vs (2 workers, explicit pool)",
        "",
        "e2e ms, diner send to kitchen frame (last client). Medians are over runs. Before = `phase3-repeats.md`; after = `phase3b-*` JSON. `<100` = runs with p99 under 100 ms.",
        "",
        "| Path | Scenario | Version | runs | p99<100 | median p50 | median p95 | median p99 | worst p99 | p99 per run |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for path in ("host", "in-network"):
        for scen in ("A sequential", "C 50/s", "C 100/s", "C 200/s"):
            for ver, d in (("before", b), ("after", a)):
                rs = d.get((path, scen), [])
                if ver == "before" and path == "in-network":
                    rs = [r for r in rs if not r["run"].endswith("cgroup")] or rs
                out.append(f"| {path} | {scen} | {ver} | {summ(rs)} |")
    (RES / "phase3b-repeats.md").write_text("\n".join(out) + "\n")
    print("\n".join(out))


if __name__ == "__main__":
    main()
