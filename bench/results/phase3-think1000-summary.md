# Phase 3 latency benchmark: summary

Run: 2026-10-09 22:43:00 PDT on Apple M2 Pro, 16 GiB RAM; Docker VM: 10 CPUs, 8217382912 bytes, Docker Desktop, 29.2.0.
All times in milliseconds. e2e = kitchen frame received minus (intended) POST send time, same host clock. Percentiles are nearest-rank over every order (no outlier trimming). `errors` = HTTP failures + frames never received; `late` = frames that took more than 2 s (still included in the percentiles).

## End-to-end: diner tap to kitchen receives (last client of the fan-out)

| Scenario | n | errors | mean | p50 | p90 | p95 | p99 | max | min |
|---|---|---|---|---|---|---|---|---|---|
| A sequential, 1 client | 100 | 0 | 27.70 | 27.11 | 35.82 | 44.92 | 57.91 | 73.12 | 7.39 |

## POST round trip (what the diner's request takes)

| Scenario | n | errors | mean | p50 | p90 | p95 | p99 | max | min |
|---|---|---|---|---|---|---|---|---|---|
| A sequential, 1 client | 100 | 0 | 28.26 | 27.96 | 35.55 | 44.95 | 57.67 | 74.30 | 8.77 |

## Publish to receive (envelope published_at_us to client frame; needs host/VM clocks to agree)

| Scenario | n | errors | mean | p50 | p90 | p95 | p99 | max | min |
|---|---|---|---|---|---|---|---|---|---|
| A sequential, 1 client | 100 | 0 | 5.36 | 5.41 | 7.56 | 7.87 | 9.40 | 10.87 | 2.43 |

## Clock check (scenario A)

published_at minus send: p50 21.60 ms, min 4.85 ms; response minus published_at: p50 5.87 ms, min 3.12 ms; orders where either was negative: 0. A negative value would mean the Docker VM clock disagrees with the host clock.

