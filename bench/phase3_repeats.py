# ruff: noqa: E501
"""Compare repeated runs of bench/e2e_latency.py --suite (run-to-run variance table).

Run: python3 bench/phase3_repeats.py -run1 -run2 ""     # labels as passed to --label ("" = canonical files)
Writes bench/results/phase3-repeats.md. Pure stdlib.
"""

import json
import sys
from pathlib import Path

RES = Path(__file__).resolve().parent / "results"


def load(label: str, scen: str):
    p = RES / f"phase3{label}-{scen}.json"
    return json.loads(p.read_text()) if p.exists() else None


def f(x: float | None) -> str:
    return "-" if x is None else (f"{x:.1f}" if x >= 100 else f"{x:.2f}")


def gather(labels: list[str], prefix: str) -> list[tuple[str, str, dict]]:
    rows = []
    for lab in labels:
        name = lab.lstrip("-") or "final"
        a, b, c, d = (load(lab, s) for s in "ABCD")
        if a:
            rows.append((f"{prefix}A sequential", name, a["summary"]["e2e_last_ms"]))
        for k, r in (b or {}).items():
            rows.append((f"{prefix}B {k} clients (last)", name, r["summary"]["e2e_last_ms"]))
        for k, r in (c or {}).items():
            if r["summary"].get("e2e_last_ms"):
                rows.append((f"{prefix}C {k}/s", name, r["summary"]["e2e_last_ms"]))
        if d:
            rows.append((f"{prefix}D status path", name, d["summary"]["e2e_ms"]))
    return rows


def main() -> None:
    labels = sys.argv[1:] or ["-run1", "-run2", ""]
    # runs made from inside the compose network (no Docker Desktop port forward) are labelled -docker*
    rows = gather([x for x in labels if not x.startswith("-docker")], "host: ")
    rows += gather([x for x in labels if x.startswith("-docker")], "in-network: ")
    out = [
        "# Phase 3: run-to-run variance",
        "",
        "Same suite, same machine, repeated. e2e in ms (diner send to kitchen frame, last client).",
        "",
        "| Scenario | run | n | errors | p50 | p95 | p99 | max |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for scen, run, s in rows:
        out.append(
            f"| {scen} | {run} | {s['count']} | {s['errors']} | {f(s['p50'])} | {f(s['p95'])} "
            f"| {f(s['p99'])} | {f(s['max'])} |"
        )
    out += [
        "",
        "## Runs with p99 under 100 ms",
        "",
        "| Scenario | passes | runs |",
        "|---|---|---|",
    ]
    by: dict[str, list[dict]] = {}
    for scen, _, s in rows:
        by.setdefault(scen, []).append(s)
    for scen, ss in by.items():
        ok = sum(1 for s in ss if s["p99"] < 100)
        out.append(f"| {scen} | {ok} | {len(ss)} |")
    text = "\n".join(out) + "\n"
    (RES / "phase3-repeats.md").write_text(text)
    sys.stdout.write(text)


if __name__ == "__main__":
    main()
