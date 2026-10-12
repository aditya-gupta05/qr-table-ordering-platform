# Phase 3 latency benchmark: summary

Run: 2026-10-09 22:42:09 PDT on Apple M2 Pro, 16 GiB RAM; Docker VM: 10 CPUs, 8217382912 bytes, Docker Desktop, 29.2.0.
All times in milliseconds. e2e = kitchen frame received minus (intended) POST send time, same host clock. Percentiles are nearest-rank over every order (no outlier trimming). `errors` = HTTP failures + frames never received; `late` = frames that took more than 2 s (still included in the percentiles).

## End-to-end: diner tap to kitchen receives (last client of the fan-out)

| Scenario | n | errors | mean | p50 | p90 | p95 | p99 | max | min |
|---|---|---|---|---|---|---|---|---|---|
| A sequential, 1 client | 300 | 0 | 21.10 | 21.72 | 27.59 | 30.12 | 38.11 | 53.91 | 7.17 |

## POST round trip (what the diner's request takes)

| Scenario | n | errors | mean | p50 | p90 | p95 | p99 | max | min |
|---|---|---|---|---|---|---|---|---|---|
| A sequential, 1 client | 300 | 0 | 22.14 | 22.96 | 29.48 | 31.74 | 37.98 | 54.94 | 7.86 |

## Publish to receive (envelope published_at_us to client frame; needs host/VM clocks to agree)

| Scenario | n | errors | mean | p50 | p90 | p95 | p99 | max | min |
|---|---|---|---|---|---|---|---|---|---|
| A sequential, 1 client | 300 | 0 | 5.28 | 5.07 | 7.73 | 8.55 | 9.94 | 28.81 | 1.83 |

## Clock check (scenario A)

published_at minus send: p50 16.25 ms, min 2.67 ms; response minus published_at: p50 6.34 ms, min 2.35 ms; orders where either was negative: 0. A negative value would mean the Docker VM clock disagrees with the host clock.

