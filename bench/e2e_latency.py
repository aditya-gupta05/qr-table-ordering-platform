# ruff: noqa: E501, ASYNC109, ASYNC110, ASYNC240
"""End-to-end order-notification latency benchmark (Phase 3).

Measures "diner taps Place order" -> "kitchen WebSocket receives order.created" on one host clock,
plus the diner live-status path (PATCH -> order.status_changed on /ws/orders/{public_id}).

Run (stack must be up, `make up && make seed`):
    cd api && uv run python ../bench/e2e_latency.py --suite
    cd api && uv run python ../bench/e2e_latency.py --scenario A --scenario D
Results: bench/results/phase3<label>-{A,B,C,D}.json (raw per-order samples) + phase3<label>-summary.md

Scenarios
  A  sequential baseline: 50 warmup (discarded) + 1000 orders, 1 kitchen client
  B  fan-out: 300 sequential orders with 1, 10, 100 kitchen clients (e2e to the LAST client + median client)
  C  open loop: 10/50/100/200 orders/s for 20 s, 10 kitchen clients, latency from the INTENDED send time
  D  status path: PATCH received->preparing for 300 orders, PATCH send -> frame on /ws/orders/{public_id}

Honesty rules baked in: scenario C schedules sends on a fixed timetable regardless of response time and
measures from the intended time (no coordinated omission); nothing is dropped from percentiles except the
discarded warmup; frames that never arrive are counted as errors, frames later than 2 s are counted as
"late" but still contribute their real latency to the percentiles.

Every order carries notes='bench-phase3' and is deleted in a finally block.
"""

import argparse
import asyncio
import json
import math
import os
import platform
import random
import statistics
import subprocess
import sys
import time
import uuid
from pathlib import Path

import asyncpg
import httpx
from websockets.asyncio.client import connect

TAG = "bench-phase3"
TIMEOUT_S = 2.0  # a frame not seen within this long is "late"; never seen at all is "lost"
PROM_SCRAPE_S = 5  # must match ops/prometheus/prometheus.yml scrape_interval
now_ns = time.time_ns


def out(msg: str = "") -> None:
    sys.stdout.write(msg + "\n")
    sys.stdout.flush()


# --------------------------------------------------------------------------- statistics


def pct(sorted_vals: list[float], p: float) -> float:
    """Nearest-rank percentile (p in 0..1)."""
    k = max(0, min(len(sorted_vals) - 1, math.ceil(p * len(sorted_vals)) - 1))
    return sorted_vals[k]


def summ(vals: list[float], errors: int = 0) -> dict:
    sv = sorted(vals)
    if not sv:
        return {"count": 0, "errors": errors}
    return {
        "count": len(sv),
        "errors": errors,
        "mean": statistics.fmean(sv),
        "min": sv[0],
        "p50": pct(sv, 0.50),
        "p90": pct(sv, 0.90),
        "p95": pct(sv, 0.95),
        "p99": pct(sv, 0.99),
        "max": sv[-1],
    }


def fmt(x: float | None) -> str:
    if x is None:
        return "-"
    return f"{x:.1f}" if abs(x) >= 100 else f"{x:.2f}"


# --------------------------------------------------------------------------- kitchen listeners


class Collector:
    """Records the first arrival time of each order.created per kitchen client.

    A frame can arrive BEFORE the POST response (the API publishes before it returns), so frames are
    keyed by public_id and matched to POSTs afterwards.
    """

    def __init__(self) -> None:
        self.recv: dict[str, dict[int, int]] = {}
        self.published_us: dict[str, int] = {}
        self._waiters: dict[str, tuple[int, asyncio.Future]] = {}

    def on_message(self, idx: int, t_ns: int, raw: str | bytes) -> None:
        env = json.loads(raw)
        if env.get("type") != "order.created":
            return
        pid = env["order"]["public_id"]
        self.published_us[pid] = env["published_at_us"]
        got = self.recv.setdefault(pid, {})
        got.setdefault(idx, t_ns)
        waiter = self._waiters.get(pid)
        if waiter and len(got) >= waiter[0] and not waiter[1].done():
            waiter[1].set_result(True)

    async def wait(self, pid: str, need: int, timeout: float) -> bool:
        if len(self.recv.get(pid, ())) >= need:
            return True
        fut = asyncio.get_running_loop().create_future()
        self._waiters[pid] = (need, fut)
        try:
            await asyncio.wait_for(fut, timeout)
            return True
        except TimeoutError:
            return False
        finally:
            self._waiters.pop(pid, None)


async def _kitchen_client(idx: int, url: str, col: Collector, opened: asyncio.Event) -> None:
    async with connect(url, compression=None, max_size=None, ping_interval=None) as ws:
        opened.set()
        async for raw in ws:
            col.on_message(idx, now_ns(), raw)  # stamp first, parse inside


class KitchenFleet:
    def __init__(self, ws_base: str, n: int, col: Collector) -> None:
        self.tasks: list[asyncio.Task] = []
        self.ws_base, self.n, self.col = ws_base, n, col

    async def __aenter__(self):
        events = []
        for i in range(self.n):
            ev = asyncio.Event()
            events.append(ev)
            self.tasks.append(
                asyncio.create_task(_kitchen_client(i, f"{self.ws_base}/ws/kitchen", self.col, ev))
            )
        await asyncio.wait_for(asyncio.gather(*(e.wait() for e in events)), 30)
        await asyncio.sleep(0.2)  # let the hub register all subscriptions
        return self

    async def __aexit__(self, *exc):
        for t in self.tasks:
            t.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)


# --------------------------------------------------------------------------- order helpers


class Ctx:
    def __init__(
        self, client: httpx.AsyncClient, tokens: list[str], item_ids: list[int], seed: int
    ):
        self.client, self.tokens, self.item_ids = client, tokens, item_ids
        self.rng = random.Random(seed)

    def body(self) -> bytes:
        """A realistic cart: 2-3 distinct lines, qty 1-2."""
        lines = [
            {"menu_item_id": i, "qty": self.rng.randint(1, 2)}
            for i in self.rng.sample(self.item_ids, self.rng.choice([2, 3]))
        ]
        return json.dumps(
            {"table_token": self.rng.choice(self.tokens), "items": lines, "notes": TAG}
        ).encode()

    async def post(self, content: bytes) -> tuple[int, dict | None, int]:
        """Returns (status, json, t_resp_ns). Status 0 = transport error/timeout."""
        try:
            r = await self.client.post(
                "/api/orders",
                content=content,
                headers={"Idempotency-Key": str(uuid.uuid4()), "Content-Type": "application/json"},
            )
            t = now_ns()
            return r.status_code, (r.json() if r.status_code == 201 else None), t
        except Exception as exc:
            return 0, {"error": repr(exc)}, now_ns()


def collect(orders: list[dict], col: Collector, nclients: int) -> tuple[list[dict], dict]:
    """Join POST records with frame arrival times. Returns (samples, error counts)."""
    samples, http_err, lost, late = [], 0, 0, 0
    for o in orders:
        s = {
            "i": o["i"],
            "t_send_ns": o["t_send_ns"],
            "post_rtt_ms": (o["t_resp_ns"] - o["t_send_ns"]) / 1e6,
        }
        if "t_actual_ns" in o:
            s["gen_lag_ms"] = (o["t_actual_ns"] - o["t_send_ns"]) / 1e6
        if o["status"] != 201:
            http_err += 1
            s["error"] = f"http {o['status']}"
            samples.append(s)
            continue
        pid = o["public_id"]
        times = sorted(col.recv.get(pid, {}).values())
        pub_ns = col.published_us.get(pid, 0) * 1000
        s["published_at_us"] = col.published_us.get(pid)
        s["t_resp_ns"] = o["t_resp_ns"]
        if len(times) < nclients:
            lost += 1
            s["error"] = "lost"
            samples.append(s)
            continue
        s["e2e_last_ms"] = (times[-1] - o["t_send_ns"]) / 1e6
        s["e2e_median_ms"] = (times[len(times) // 2] - o["t_send_ns"]) / 1e6
        s["e2e_first_ms"] = (times[0] - o["t_send_ns"]) / 1e6
        s["pub_to_recv_last_ms"] = (times[-1] - pub_ns) / 1e6
        s["pub_to_recv_median_ms"] = (times[len(times) // 2] - pub_ns) / 1e6
        if s["e2e_last_ms"] > TIMEOUT_S * 1000:
            late += 1
        samples.append(s)
    return samples, {"http_errors": http_err, "lost": lost, "late_gt_2s": late}


METRICS = ["e2e_last_ms", "e2e_median_ms", "post_rtt_ms", "pub_to_recv_last_ms", "gen_lag_ms"]


def summarize(samples: list[dict], errs: dict) -> dict:
    nerr = errs["http_errors"] + errs["lost"]
    res = {}
    for m in METRICS:
        vals = [s[m] for s in samples if m in s and "error" not in s]
        if vals:
            res[m] = summ(vals, nerr)
    res["errors"] = errs
    return res


def skew_check(samples: list[dict]) -> dict:
    """published_at (API/VM clock) must lie between t_send and t_resp (host clock) when sequential."""
    ok = [s for s in samples if s.get("published_at_us")]
    a = [(s["published_at_us"] * 1000 - s["t_send_ns"]) / 1e6 for s in ok]
    b = [(s["t_resp_ns"] - s["published_at_us"] * 1000) / 1e6 for s in ok]
    if not a:
        return {}
    return {
        "send_to_published_ms": summ(a),
        "published_to_resp_ms": summ(b),
        "negative_either_side": sum(1 for x, y in zip(a, b, strict=True) if x < 0 or y < 0),
    }


# --------------------------------------------------------------------------- scenarios


async def run_sequential(
    ctx: Ctx, ws_base: str, n: int, warmup: int, nclients: int, think_ms: float = 0
) -> dict:
    col = Collector()
    orders: list[dict] = []
    t_wall = time.perf_counter()
    async with KitchenFleet(ws_base, nclients, col):
        for i in range(warmup + n):
            if think_ms:
                await asyncio.sleep(think_ms / 1000)  # idle gap between orders (diagnostic)
            content = ctx.body()
            t_send = now_ns()
            status, js, t_resp = await ctx.post(content)
            rec = {"i": i - warmup, "t_send_ns": t_send, "t_resp_ns": t_resp, "status": status}
            if status == 201:
                rec["public_id"] = js["public_id"]
                await col.wait(js["public_id"], nclients, TIMEOUT_S)
            if i >= warmup:
                orders.append(rec)
    samples, errs = collect(orders, col, nclients)
    return {
        "params": {"orders": n, "warmup_discarded": warmup, "kitchen_clients": nclients},
        "wall_s": time.perf_counter() - t_wall,
        "summary": summarize(samples, errs),
        "samples": samples,
    }


async def docker_stats_sampler(stop: asyncio.Event, acc: dict) -> None:
    fmt_s = "{{.Name}}|{{.CPUPerc}}|{{.MemUsage}}"
    while not stop.is_set():
        try:
            p = await asyncio.create_subprocess_exec(
                "docker",
                "stats",
                "--no-stream",
                "--format",
                fmt_s,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            data, _ = await p.communicate()
            for line in data.decode().splitlines():
                name, cpu, mem = line.split("|")
                name = name.removeprefix("qr-ordering-").removesuffix("-1")
                acc.setdefault(name, []).append(
                    {"cpu_pct": float(cpu.rstrip("%")), "mem": mem.split("/")[0].strip()}
                )
        except Exception:
            return
        await asyncio.sleep(0.3)


async def pg_activity_sampler(stop: asyncio.Event, acc: dict, db_url: str) -> None:
    """Counts Postgres backends by state / wait event while the load runs (diagnostic runs only)."""
    conn = await asyncpg.connect(db_url)
    try:
        while not stop.is_set():
            rows = await conn.fetch(
                "select coalesce(state, 'background') as st, coalesce(wait_event_type, '-') as w, "
                "count(*) as n from pg_stat_activity "
                "where datname = current_database() and pid <> pg_backend_pid() group by 1, 2"
            )
            acc.setdefault("samples", []).append({f"{r['st']}/{r['w']}": r["n"] for r in rows})
            await asyncio.sleep(0.25)
    finally:
        await conn.close()


async def run_open_loop(
    ctx: Ctx, ws_base: str, rate: int, duration: float, nclients: int, diag_db_url: str | None
) -> dict:
    col = Collector()
    n = int(rate * duration)
    orders: list[dict] = []
    stats: dict = {}
    pg: dict = {}
    stop = asyncio.Event()
    async with KitchenFleet(ws_base, nclients, col):
        # short warmup at low rate (discarded) so connection pools exist before the timetable starts
        for _ in range(20):
            _, js, _ = await ctx.post(ctx.body())
            if js and "public_id" in js:
                await col.wait(js["public_id"], nclients, TIMEOUT_S)
        samplers = (
            [
                asyncio.create_task(docker_stats_sampler(stop, stats)),
                asyncio.create_task(pg_activity_sampler(stop, pg, diag_db_url)),
            ]
            if diag_db_url
            else []
        )
        cpu0, wall0 = time.process_time(), time.perf_counter()

        # The timetable lives on the SAME clock the samples are stamped with (wall, time_ns). An earlier
        # version scheduled on the loop's monotonic clock; macOS slews the wall clock, so intended and
        # actual send times drifted apart by ~0.8 ms/s and e2e went negative.
        t0_ns = now_ns() + 500_000_000

        async def send(i: int, content: bytes) -> None:
            t_actual = now_ns()
            status, js, t_resp = await ctx.post(content)
            rec = {
                "i": i,
                "t_send_ns": t0_ns + int(i * 1e9 / rate),  # INTENDED time, not t_actual
                "t_actual_ns": t_actual,
                "t_resp_ns": t_resp,
                "status": status,
            }
            if status == 201:
                rec["public_id"] = js["public_id"]
            orders.append(rec)

        bodies = [ctx.body() for _ in range(n)]  # build off the timetable
        tasks = []
        for i in range(n):
            delay = (t0_ns + i * 1e9 / rate - now_ns()) / 1e9
            if delay > 0:
                await asyncio.sleep(delay)
            tasks.append(asyncio.create_task(send(i, bodies[i])))
        await asyncio.gather(*tasks)
        send_wall = time.perf_counter() - wall0

        # drain: wait up to 2 s past the last response for stragglers
        ok = [o for o in orders if o["status"] == 201]
        deadline = (max((o["t_resp_ns"] for o in ok), default=now_ns()) / 1e9) + TIMEOUT_S
        while time.time() < deadline and any(
            len(col.recv.get(o["public_id"], ())) < nclients for o in ok
        ):
            await asyncio.sleep(0.05)
        client_cpu = (time.process_time() - cpu0) / (time.perf_counter() - wall0)
        stop.set()
        await asyncio.gather(*samplers, return_exceptions=True)
    orders.sort(key=lambda o: o["i"])
    samples, errs = collect(orders, col, nclients)
    achieved = n / max(1e-9, (max(o["t_resp_ns"] for o in orders) - orders[0]["t_send_ns"]) / 1e9)
    docker = {}
    for name, rows in stats.items():
        cpus = [r["cpu_pct"] for r in rows]
        docker[name] = {
            "cpu_pct_max": max(cpus),
            "cpu_pct_mean": statistics.fmean(cpus),
            "samples": len(cpus),
        }
    pg_mean = {}
    for snap in pg.get("samples", []):
        for k, v in snap.items():
            pg_mean[k] = pg_mean.get(k, 0) + v
    pg_mean = {k: v / len(pg["samples"]) for k, v in pg_mean.items()} if pg.get("samples") else {}
    return {
        "pg_backends_mean": pg_mean,
        "params": {
            "target_rate": rate,
            "duration_s": duration,
            "orders": n,
            "kitchen_clients": nclients,
            "send_phase_wall_s": send_wall,
            "completed_orders_per_s": achieved,
            "bench_process_cpu_fraction": client_cpu,
        },
        "docker_stats": docker,
        "summary": summarize(samples, errs),
        "samples": samples,
    }


async def run_status_path(ctx: Ctx, ws_base: str, n: int, warmup: int) -> dict:
    created = []
    for _ in range(n + warmup):
        status, js, _ = await ctx.post(ctx.body())
        if status != 201:
            raise RuntimeError(f"setup order failed: {status} {js}")
        created.append(js)
    samples, errors = [], 0
    for i, o in enumerate(created):
        async with connect(
            f"{ws_base}/ws/orders/{o['public_id']}", compression=None, ping_interval=None
        ) as ws:
            await asyncio.sleep(0.01)  # subscription registered before the PATCH

            async def recv(ws=ws):
                raw = await ws.recv()
                return now_ns(), json.loads(raw)

            rt = asyncio.create_task(recv())
            t_send = now_ns()
            r = await ctx.client.patch(
                f"/api/kitchen/orders/{o['id']}/status", json={"status": "preparing"}
            )
            t_resp = now_ns()
            try:
                t_recv, env = await asyncio.wait_for(rt, TIMEOUT_S)
            except TimeoutError:
                if i >= warmup:
                    errors += 1
                continue
            if r.status_code != 200 or env.get("type") != "order.status_changed":
                if i >= warmup:
                    errors += 1
                continue
            if i >= warmup:
                samples.append(
                    {
                        "i": i - warmup,
                        "t_send_ns": t_send,
                        "e2e_ms": (t_recv - t_send) / 1e6,
                        "patch_rtt_ms": (t_resp - t_send) / 1e6,
                        "pub_to_recv_ms": (t_recv - env["published_at_us"] * 1000) / 1e6,
                    }
                )
    summary = {}
    for m in ("e2e_ms", "patch_rtt_ms", "pub_to_recv_ms"):
        summary[m] = summ([s[m] for s in samples], errors)
    return {
        "params": {"orders": n, "warmup_discarded": warmup, "transition": "received->preparing"},
        "summary": summary,
        "samples": samples,
    }


# --------------------------------------------------------------------------- Prometheus


class Prom:
    HIST = {
        "order_create_seconds": "order_create_seconds",
        "order_event_publish_seconds": "order_event_publish_seconds",
        "realtime_fanout_seconds": "realtime_fanout_seconds",
        "realtime_publish_to_send_seconds": "realtime_publish_to_send_seconds",
        "http_post_orders_seconds": (
            'http_request_duration_seconds{method="POST",route="/api/orders"}'
        ),
        "http_patch_status_seconds": (
            "http_request_duration_seconds"
            '{method="PATCH",route="/api/kitchen/orders/{order_id}/status"}'
        ),
    }

    def __init__(self, url: str) -> None:
        self.url = url

    async def _q(self, c: httpx.AsyncClient, q: str) -> list[dict]:
        r = await c.get(f"{self.url}/api/v1/query", params={"query": q})
        r.raise_for_status()
        return r.json()["data"]["result"]

    async def snapshot(self) -> dict:
        snap = {}
        async with httpx.AsyncClient(timeout=10) as c:
            for key, sel in self.HIST.items():
                name, _, labels = sel.partition("{")
                labels = "{" + labels if labels else ""
                buckets = {}
                for r in await self._q(c, f"sum by (le) ({name}_bucket{labels})"):
                    le = r["metric"]["le"]
                    buckets[math.inf if le == "+Inf" else float(le)] = float(r["value"][1])
                sm = await self._q(c, f"sum({name}_sum{labels})")
                ct = await self._q(c, f"sum({name}_count{labels})")
                snap[key] = {
                    "buckets": buckets,
                    "sum": float(sm[0]["value"][1]) if sm else 0.0,
                    "count": float(ct[0]["value"][1]) if ct else 0.0,
                }
        return snap


def hist_quantile(q: float, buckets: dict[float, float]) -> float | None:
    """Same interpolation as PromQL histogram_quantile (seconds)."""
    items = sorted(buckets.items())
    if not items or items[-1][1] <= 0:
        return None
    total = items[-1][1]
    rank = q * total
    prev_le, prev_c = 0.0, 0.0
    for le, c in items:
        if c >= rank:
            if math.isinf(le):
                return (
                    prev_le  # PromQL returns the highest finite bound: a LOWER bound on the truth
                )
            if c == prev_c:
                return le
            return prev_le + (le - prev_le) * (rank - prev_c) / (c - prev_c)
        prev_le, prev_c = le, c
    return None


def _ms(x: float | None) -> float | None:
    return None if x is None else x * 1000


def snap_delta(after: dict, before: dict) -> dict:
    res = {}
    for key, a in after.items():
        b = before[key]
        buckets = {le: a["buckets"].get(le, 0) - b["buckets"].get(le, 0) for le in a["buckets"]}
        count = a["count"] - b["count"]
        res[key] = {
            "count": count,
            "mean_ms": (a["sum"] - b["sum"]) / count * 1000 if count else None,
            "p50_ms": _ms(hist_quantile(0.50, buckets)),
            "p95_ms": _ms(hist_quantile(0.95, buckets)),
            "p99_ms": _ms(hist_quantile(0.99, buckets)),
            "buckets": {
                ("+Inf" if math.isinf(k) else str(k)): v for k, v in sorted(buckets.items())
            },
        }
    return res


# --------------------------------------------------------------------------- reporting


def machine_info() -> dict:
    def sh(cmd: list[str]) -> str:
        try:
            return subprocess.run(cmd, capture_output=True, text=True, timeout=10).stdout.strip()
        except Exception:
            return ""

    return {
        "platform": platform.platform(),
        "python": platform.python_version(),
        "cpu": sh(["sysctl", "-n", "machdep.cpu.brand_string"]),
        "ncpu": sh(["sysctl", "-n", "hw.ncpu"]),
        "ram_bytes": sh(["sysctl", "-n", "hw.memsize"]),
        "docker_vm": sh(
            [
                "docker",
                "info",
                "--format",
                "{{.NCPU}} CPUs, {{.MemTotal}} bytes, {{.OperatingSystem}}, {{.ServerVersion}}",
            ]
        ),
    }


HEAD = "| Scenario | n | errors | mean | p50 | p90 | p95 | p99 | max | min |"
SEP = "|---|---|---|---|---|---|---|---|---|---|"


def row(name: str, s: dict | None) -> str:
    if not s or not s.get("count"):
        return f"| {name} | 0 | {s.get('errors', 0) if s else '-'} | - | - | - | - | - | - | - |"
    return (
        f"| {name} | {s['count']} | {s['errors']} | {fmt(s['mean'])} | {fmt(s['p50'])} "
        f"| {fmt(s['p90'])} | {fmt(s['p95'])} | {fmt(s['p99'])} | {fmt(s['max'])} | {fmt(s['min'])} |"
    )


def build_summary(results: dict, server: dict, meta: dict) -> str:
    L: list[str] = []
    add = L.append
    add("# Phase 3 latency benchmark: summary")
    add("")
    add(
        f"Run: {meta['started']} on {meta['machine'].get('cpu', '?')}, "
        f"{int(meta['machine'].get('ram_bytes') or 0) / 2**30:.0f} GiB RAM; "
        f"Docker VM: {meta['machine'].get('docker_vm', '?')}."
    )
    add(
        "All times in milliseconds. e2e = kitchen frame received minus (intended) POST send time, "
        "same host clock. Percentiles are nearest-rank over every order (no outlier trimming). "
        "`errors` = HTTP failures + frames never received; `late` = frames that took more than 2 s "
        "(still included in the percentiles)."
    )
    add("")

    def block(title: str, metric: str, d_metric: str | None = None) -> None:
        add(f"## {title}")
        add("")
        add(HEAD)
        add(SEP)
        if "A" in results:
            add(row("A sequential, 1 client", results["A"]["summary"].get(metric)))
        for k, r in results.get("B", {}).items():
            add(row(f"B fan-out, {k} clients", r["summary"].get(metric)))
        for k, r in results.get("C", {}).items():
            late = r["summary"]["errors"]["late_gt_2s"]
            add(
                row(
                    f"C open-loop {k}/s" + (f" (late {late})" if late else ""),
                    r["summary"].get(metric),
                )
            )
        if d_metric and "D" in results:
            add(row("D status path", results["D"]["summary"].get(d_metric)))
        add("")

    block(
        "End-to-end: diner tap to kitchen receives (last client of the fan-out)",
        "e2e_last_ms",
        "e2e_ms",
    )
    if "B" in results:
        add("## Fan-out: median client vs last client")
        add("")
        add(HEAD)
        add(SEP)
        for k, r in results["B"].items():
            add(row(f"B {k} clients, median client", r["summary"].get("e2e_median_ms")))
            add(row(f"B {k} clients, last client", r["summary"].get("e2e_last_ms")))
        add("")
    block("POST round trip (what the diner's request takes)", "post_rtt_ms", "patch_rtt_ms")
    block(
        "Publish to receive (envelope published_at_us to client frame; needs host/VM clocks to agree)",
        "pub_to_recv_last_ms",
        "pub_to_recv_ms",
    )
    if "C" in results:
        add("## Open-loop details")
        add("")
        add(
            "| rate | completions/s | generator lag p99 (ms) | bench CPU | api CPU max/mean "
            "| realtime CPU max | redis CPU max | postgres CPU max |"
        )
        add("|---|---|---|---|---|---|---|---|")
        for k, r in results["C"].items():
            p, d = r["params"], r.get("docker_stats", {})
            lag = r["summary"].get("gen_lag_ms", {}).get("p99")

            def cp(n: str, key: str = "cpu_pct_max", d: dict = d) -> str:
                return f"{d[n][key]:.0f}%" if n in d else "-"

            add(
                f"| {k}/s | {p['completed_orders_per_s']:.1f} | {fmt(lag)} "
                f"| {p['bench_process_cpu_fraction'] * 100:.0f}% "
                f"| {cp('api')} / {cp('api', 'cpu_pct_mean')} | {cp('realtime')} | {cp('redis')} "
                f"| {cp('postgres')} |"
            )
        add("")
        for k, r in results["C"].items():
            if r.get("pg_backends_mean"):
                top = sorted(r["pg_backends_mean"].items(), key=lambda kv: -kv[1])
                add(
                    f"- {k}/s mean Postgres backends by state/wait: "
                    + ", ".join(f"{a}={b:.1f}" for a, b in top)
                )
        add("")
    if server:
        add("## Server-side histograms (Prometheus, same window; bucket-interpolated quantiles)")
        add("")
        add("| Scenario | metric | count | mean | p50 | p95 | p99 |")
        add("|---|---|---|---|---|---|---|")
        for scen, hs in server.items():
            for m, h in hs.items():
                if not h["count"]:
                    continue
                add(
                    f"| {scen} | {m} | {h['count']:.0f} | {fmt(h['mean_ms'])} | {fmt(h['p50_ms'])} "
                    f"| {fmt(h['p95_ms'])} | {fmt(h['p99_ms'])} |"
                )
        add("")
    if "A" in results and results["A"].get("clock_skew"):
        sk = results["A"]["clock_skew"]
        add("## Clock check (scenario A)")
        add("")
        add(
            f"published_at minus send: p50 {fmt(sk['send_to_published_ms']['p50'])} ms, "
            f"min {fmt(sk['send_to_published_ms']['min'])} ms; "
            f"response minus published_at: p50 {fmt(sk['published_to_resp_ms']['p50'])} ms, "
            f"min {fmt(sk['published_to_resp_ms']['min'])} ms; "
            f"orders where either was negative: {sk['negative_either_side']}. "
            "A negative value would mean the Docker VM clock disagrees with the host clock."
        )
        add("")
    budget = latency_budget(results, server)
    if budget:
        add("## Latency budget (scenario A, means)")
        add("")
        add(budget)
        add("")
    return "\n".join(L)


def latency_budget(results: dict, server: dict) -> str:
    if "A" not in results or "A" not in server:
        return ""
    sa, sv = results["A"]["summary"], server["A"]
    try:
        post_rtt = sa["post_rtt_ms"]["mean"]
        e2e = sa["e2e_last_ms"]["mean"]
        pub_to_recv = sa["pub_to_recv_last_ms"]["mean"]
        http = sv["http_post_orders_seconds"]["mean_ms"]
        create = sv["order_create_seconds"]["mean_ms"]
        publish = sv["order_event_publish_seconds"]["mean_ms"]
        p2s = sv["realtime_publish_to_send_seconds"]["mean_ms"]
        fan = sv["realtime_fanout_seconds"]["mean_ms"]
    except (KeyError, TypeError):
        return ""
    send_to_pub = (
        e2e - pub_to_recv
    )  # mean of (published_at - t_send); needs host/VM clocks to agree
    rows = [
        (
            "Client send to API `published_at`, excluding DB: network ingress (host to Docker "
            "port-forward), uvicorn parse, routing, post-commit reload, envelope build "
            "(= client mean of published_at minus t_send, minus the DB row below)",
            send_to_pub - create,
        ),
        ("API pricing queries + INSERTs + COMMIT (`order_create_seconds`, Prometheus)", create),
        (
            "`published_at` to Go frame write (`realtime_publish_to_send_seconds`, Prometheus): "
            "Redis PUBLISH + hop + Go fan-out + queue + writer",
            p2s,
        ),
        (
            "Go WS write to client read (client pub_to_recv minus publish_to_send)",
            pub_to_recv - p2s,
        ),
    ]
    L = ["| Stage | mean ms |", "|---|---|"]
    for name, v in rows:
        L.append(f"| {name} | {fmt(v)} |")
    L.append(f"| **Measured end-to-end (client, mean)** | {fmt(e2e)} |")
    L.append("")
    L.append("Reference points, not additive: ")
    L.append(f"- `order_event_publish_seconds` (API's Redis PUBLISH call): {fmt(publish)} ms")
    L.append(f"- `realtime_fanout_seconds` (Go lookup + enqueue): {fmt(fan)} ms")
    L.append(
        f"- Redis hop to Go subscriber (publish_to_send minus fanout, includes the PUBLISH): {fmt(p2s - fan)} ms"
    )
    L.append(f"- API HTTP handler total (`http_request_duration_seconds`): {fmt(http)} ms")
    L.append(
        f"- Client POST round trip: {fmt(post_rtt)} ms; port-forward + client overhead = RTT minus handler = {fmt(post_rtt - http)} ms (both directions)"
    )
    L.append("")
    L.append(
        "Rows 1-4 sum to the measured end-to-end by construction (`published_at` is the pivot). Row 1 is a "
        "residual and is only meaningful if the host and Docker VM clocks agree; see the clock check. "
        "Server means are exact (Prometheus `_sum/_count` deltas), not bucket-interpolated. The HTTP "
        "handler total includes response serialisation, which happens after the publish and is not on the "
        "notification path."
    )
    return "\n".join(L)


# --------------------------------------------------------------------------- main


async def preflight(args, client: httpx.AsyncClient) -> None:
    (await client.get("/healthz")).raise_for_status()
    http_base = args.ws.replace("wss://", "https://").replace("ws://", "http://")
    async with httpx.AsyncClient(timeout=5) as c:
        (await c.get(http_base + "/healthz")).raise_for_status()


async def cleanup(db_url: str) -> int:
    conn = await asyncpg.connect(db_url)
    try:
        await conn.execute(
            "delete from order_items where order_id in (select id from orders where notes = $1)",
            TAG,
        )
        await conn.execute("delete from orders where notes = $1", TAG)
        return await conn.fetchval("select count(*) from orders where notes = $1", TAG)
    finally:
        await conn.close()


def _show(name: str, s: dict | None) -> None:
    if s:
        out(
            f"   {name}: n={s['count']} err={s['errors']} p50={fmt(s['p50'])} p95={fmt(s['p95'])} "
            f"p99={fmt(s['p99'])} max={fmt(s['max'])} ms"
        )


def _write(d: Path, label: str, scen: str, data: dict) -> None:
    (d / f"phase3{label}-{scen}.json").write_text(json.dumps(data, default=lambda o: round(o, 4)))


async def amain(args) -> None:
    results_dir = Path(args.out)
    results_dir.mkdir(parents=True, exist_ok=True)
    scenarios = ["A", "B", "C", "D"] if args.suite else sorted(set(args.scenario))
    if not scenarios:
        sys.exit("pass --suite or --scenario A|B|C|D")

    conn = await asyncpg.connect(args.db_url)
    try:
        tokens = [
            r["qr_token"]
            for r in await conn.fetch("select qr_token from dining_tables order by id")
        ]
    finally:
        await conn.close()

    meta = {
        "started": time.strftime("%Y-%m-%d %H:%M:%S %Z"),
        "machine": machine_info(),
        "args": vars(args),
    }
    results: dict = {}
    server: dict = {}
    prom = None if args.no_prom else Prom(args.prom)

    limits = httpx.Limits(max_connections=2000, max_keepalive_connections=200)
    try:
        async with httpx.AsyncClient(
            base_url=args.base, timeout=httpx.Timeout(15.0, pool=None), limits=limits
        ) as client:
            await preflight(args, client)
            menu = (await client.get("/api/menu")).json()
            item_ids = [i["id"] for cat in menu for i in cat["items"] if i["is_available"]]
            if len(item_ids) < 3 or not tokens:
                sys.exit("need seeded menu and tables (make seed)")
            ctx = Ctx(client, tokens, item_ids, seed=args.seed)

            prev = await prom.snapshot() if prom else None

            async def record(label: str) -> None:
                nonlocal prev
                if not prom:
                    return
                await asyncio.sleep(PROM_SCRAPE_S + 1.5)  # let the post-run scrape land
                cur = await prom.snapshot()
                server[label] = snap_delta(cur, prev)
                prev = cur

            if "A" in scenarios:
                out("== A: sequential baseline ==")
                r = await run_sequential(ctx, args.ws, args.a_orders, 50, 1, args.a_think_ms)
                r["clock_skew"] = skew_check(r["samples"])
                results["A"] = r
                _show("A", r["summary"].get("e2e_last_ms"))
                await record("A")
                _write(results_dir, args.label, "A", r)

            if "B" in scenarios:
                results["B"] = {}
                for n in args.fanout:
                    out(f"== B: fan-out to {n} clients ==")
                    r = await run_sequential(ctx, args.ws, args.b_orders, 20, n)
                    results["B"][str(n)] = r
                    _show(f"B{n}", r["summary"].get("e2e_last_ms"))
                    await record(f"B{n}")
                _write(results_dir, args.label, "B", results["B"])

            if "C" in scenarios:
                results["C"] = {}
                for rate in args.rates:
                    out(f"== C: open loop {rate}/s for {args.duration}s ==")
                    r = await run_open_loop(
                        ctx, args.ws, rate, args.duration, 10, args.db_url if args.diag else None
                    )
                    results["C"][str(rate)] = r
                    _show(f"C{rate}", r["summary"].get("e2e_last_ms"))
                    out(
                        f"   errors={r['summary']['errors']} "
                        f"completed/s={r['params']['completed_orders_per_s']:.1f}"
                    )
                    await record(f"C{rate}")
                    _write(results_dir, args.label, "C", results["C"])

            if "D" in scenarios:
                out("== D: status-change path ==")
                r = await run_status_path(ctx, args.ws, args.d_orders, 20)
                results["D"] = r
                _show("D", r["summary"].get("e2e_ms"))
                await record("D")
                _write(results_dir, args.label, "D", r)
    finally:
        left = await cleanup(args.db_url)
        out(f"cleanup: select count(*) from orders where notes='{TAG}' -> {left}")
        if results:
            md = build_summary(results, server, meta)
            (results_dir / f"phase3{args.label}-summary.md").write_text(md + "\n")
            (results_dir / f"phase3{args.label}-server.json").write_text(
                json.dumps(server, indent=1)
            )
            out("")
            out(md)


def main() -> None:
    here = Path(__file__).resolve().parent
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--suite", action="store_true", help="run A, B, C, D and write results")
    ap.add_argument("--scenario", action="append", default=[], choices=list("ABCD"))
    ap.add_argument("--base", default="http://localhost:8000")
    ap.add_argument("--ws", default="ws://localhost:8080")
    ap.add_argument("--prom", default="http://localhost:9090")
    ap.add_argument("--no-prom", action="store_true")
    ap.add_argument(
        "--diag",
        action="store_true",
        help="scenario C only: also sample `docker stats` and pg_stat_activity. Off for headline "
        "runs because polling docker perturbs the very latency being measured.",
    )
    ap.add_argument(
        "--db-url", default=os.environ.get("BENCH_DB_URL", "postgresql://qr:qr@localhost:55432/qr")
    )
    ap.add_argument("--out", default=str(here / "results"))
    ap.add_argument(
        "--label", default="", help="suffix after 'phase3' in output names, e.g. -docker"
    )
    ap.add_argument("--a-orders", type=int, default=1000)
    ap.add_argument(
        "--a-think-ms", type=float, default=0, help="idle gap before each order in A (diagnostic)"
    )
    ap.add_argument("--b-orders", type=int, default=300)
    ap.add_argument("--d-orders", type=int, default=300)
    ap.add_argument("--fanout", type=int, nargs="+", default=[1, 10, 100])
    ap.add_argument("--rates", type=int, nargs="+", default=[10, 50, 100, 200])
    ap.add_argument("--duration", type=float, default=20.0)
    ap.add_argument("--seed", type=int, default=3)
    asyncio.run(amain(ap.parse_args()))


if __name__ == "__main__":
    main()
