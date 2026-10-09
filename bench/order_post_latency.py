"""Sequential POST /api/orders latency (unique idempotency keys). Phase 1 baseline.

Run: cd api && uv run python ../bench/order_post_latency.py [--n 200] [--base http://localhost:8000]
Orders are tagged notes="bench-phase1" so they can be deleted afterwards.
"""

import argparse
import statistics
import sys
import time
import uuid

import httpx


def pct(sorted_vals: list[float], p: float) -> float:
    return sorted_vals[min(len(sorted_vals) - 1, int(len(sorted_vals) * p))]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=200)
    ap.add_argument("--base", default="http://localhost:8000")
    ap.add_argument("--token", required=True, help="a dining table qr_token")
    args = ap.parse_args()

    with httpx.Client(base_url=args.base, timeout=10) as c:
        menu = c.get("/api/menu").json()
        item_ids = [i["id"] for cat in menu for i in cat["items"]][:3]
        lat: list[float] = []
        for _ in range(args.n):
            body = {
                "table_token": args.token,
                "items": [{"menu_item_id": i, "qty": 1} for i in item_ids],
                "notes": "bench-phase1",
            }
            t0 = time.perf_counter()
            r = c.post("/api/orders", json=body, headers={"Idempotency-Key": str(uuid.uuid4())})
            lat.append((time.perf_counter() - t0) * 1000)
            if r.status_code != 201:
                sys.exit(f"unexpected {r.status_code}: {r.text}")
    lat.sort()
    sys.stdout.write(
        f"n={len(lat)} lines_per_order={len(item_ids)} "
        f"p50={statistics.median(lat):.1f}ms p95={pct(lat, 0.95):.1f}ms "
        f"p99={pct(lat, 0.99):.1f}ms max={lat[-1]:.1f}ms\n"
    )


if __name__ == "__main__":
    main()
