# ruff: noqa: E501
"""Open-loop GET latency floor: how much of the tail is the host/VM path, not the app? (Phase 3 diagnostic)

Sends GET /healthz (no DB, no Redis) on a fixed timetable and measures from the intended send time, exactly
like scenario C of e2e_latency.py. If this trivial endpoint shows the same 100+ ms stalls, the tail is not
the order code path.

Run: cd api && uv run python ../bench/healthz_open_loop.py [--rate 50] [--duration 20] [--base URL]
"""

import argparse
import asyncio
import math
import sys
import time

import httpx


def pct(sv: list[float], p: float) -> float:
    return sv[max(0, min(len(sv) - 1, math.ceil(p * len(sv)) - 1))]


async def run(base: str, rate: int, duration: float) -> list[float]:
    lat: list[float] = []
    async with httpx.AsyncClient(
        base_url=base,
        limits=httpx.Limits(max_connections=2000),
        timeout=httpx.Timeout(15, pool=None),
    ) as c:
        await c.get("/healthz")
        t0 = time.time_ns() + 500_000_000
        n = int(rate * duration)

        async def one(i: int) -> None:
            intended = t0 + int(i * 1e9 / rate)
            await c.get("/healthz")
            lat.append((time.time_ns() - intended) / 1e6)

        tasks = []
        for i in range(n):
            delay = (t0 + i * 1e9 / rate - time.time_ns()) / 1e9
            if delay > 0:
                await asyncio.sleep(delay)
            tasks.append(asyncio.create_task(one(i)))
        await asyncio.gather(*tasks)
    return sorted(lat)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://localhost:8000")
    ap.add_argument("--rate", type=int, default=50)
    ap.add_argument("--duration", type=float, default=20)
    ap.add_argument("--repeat", type=int, default=3)
    a = ap.parse_args()
    for r in range(a.repeat):
        lat = asyncio.run(run(a.base, a.rate, a.duration))
        sys.stdout.write(
            f"healthz open-loop {a.rate}/s run {r + 1}: n={len(lat)} p50={pct(lat, 0.5):.2f} "
            f"p95={pct(lat, 0.95):.2f} p99={pct(lat, 0.99):.2f} max={lat[-1]:.2f} ms\n"
        )


if __name__ == "__main__":
    main()
