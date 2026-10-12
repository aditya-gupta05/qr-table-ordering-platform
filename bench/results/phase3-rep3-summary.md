# Phase 3 latency benchmark: summary

Run: 2026-10-09 22:51:12 PDT on Apple M2 Pro, 16 GiB RAM; Docker VM: 10 CPUs, 8217382912 bytes, Docker Desktop, 29.2.0.
All times in milliseconds. e2e = kitchen frame received minus (intended) POST send time, same host clock. Percentiles are nearest-rank over every order (no outlier trimming). `errors` = HTTP failures + frames never received; `late` = frames that took more than 2 s (still included in the percentiles).

## End-to-end: diner tap to kitchen receives (last client of the fan-out)

| Scenario | n | errors | mean | p50 | p90 | p95 | p99 | max | min |
|---|---|---|---|---|---|---|---|---|---|
| C open-loop 50/s | 1000 | 0 | 18.95 | 11.13 | 19.20 | 46.71 | 209.7 | 287.2 | 6.15 |
| C open-loop 100/s | 2000 | 0 | 36.18 | 7.81 | 78.67 | 196.1 | 545.4 | 900.3 | 4.91 |

## POST round trip (what the diner's request takes)

| Scenario | n | errors | mean | p50 | p90 | p95 | p99 | max | min |
|---|---|---|---|---|---|---|---|---|---|
| C open-loop 50/s | 1000 | 0 | 19.31 | 11.18 | 19.30 | 49.85 | 212.3 | 296.5 | 6.08 |
| C open-loop 100/s | 2000 | 0 | 37.31 | 7.86 | 85.26 | 205.1 | 549.7 | 903.1 | 4.58 |

## Publish to receive (envelope published_at_us to client frame; needs host/VM clocks to agree)

| Scenario | n | errors | mean | p50 | p90 | p95 | p99 | max | min |
|---|---|---|---|---|---|---|---|---|---|
| C open-loop 50/s | 1000 | 0 | 3.49 | 3.22 | 4.62 | 5.38 | 8.14 | 30.98 | 1.58 |
| C open-loop 100/s | 2000 | 0 | 3.01 | 2.46 | 3.90 | 5.53 | 15.21 | 143.1 | 1.13 |

## Open-loop details

| rate | completions/s | generator lag p99 (ms) | bench CPU | api CPU max/mean | realtime CPU max | redis CPU max | postgres CPU max |
|---|---|---|---|---|---|---|---|
| 50/s | 50.0 | 4.29 | 16% | - / - | - | - | - |
| 100/s | 100.0 | 25.75 | 26% | - / - | - | - | - |


