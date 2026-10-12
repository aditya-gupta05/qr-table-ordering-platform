# Phase 3 latency benchmark: summary

Run: 2026-10-09 22:52:39 PDT on Apple M2 Pro, 16 GiB RAM; Docker VM: 10 CPUs, 8217382912 bytes, Docker Desktop, 29.2.0.
All times in milliseconds. e2e = kitchen frame received minus (intended) POST send time, same host clock. Percentiles are nearest-rank over every order (no outlier trimming). `errors` = HTTP failures + frames never received; `late` = frames that took more than 2 s (still included in the percentiles).

## End-to-end: diner tap to kitchen receives (last client of the fan-out)

| Scenario | n | errors | mean | p50 | p90 | p95 | p99 | max | min |
|---|---|---|---|---|---|---|---|---|---|
| C open-loop 50/s | 1000 | 0 | 14.72 | 10.56 | 13.08 | 15.14 | 211.8 | 337.0 | 6.23 |
| C open-loop 100/s (late 1305) | 1999 | 1 | 5859.0 | 4342.8 | 14385.0 | 16659.0 | 20117.1 | 22403.1 | 5.77 |

## POST round trip (what the diner's request takes)

| Scenario | n | errors | mean | p50 | p90 | p95 | p99 | max | min |
|---|---|---|---|---|---|---|---|---|---|
| C open-loop 50/s | 1000 | 0 | 14.84 | 10.59 | 13.19 | 15.02 | 212.8 | 341.3 | 6.42 |
| C open-loop 100/s (late 1305) | 1999 | 1 | 5954.2 | 4507.5 | 14392.8 | 16712.3 | 20197.7 | 22411.8 | 5.72 |

## Publish to receive (envelope published_at_us to client frame; needs host/VM clocks to agree)

| Scenario | n | errors | mean | p50 | p90 | p95 | p99 | max | min |
|---|---|---|---|---|---|---|---|---|---|
| C open-loop 50/s | 1000 | 0 | 2.73 | 2.57 | 3.55 | 3.97 | 5.64 | 45.23 | 1.10 |
| C open-loop 100/s (late 1305) | 1999 | 1 | 53.97 | 25.92 | 129.8 | 202.6 | 401.7 | 1014.9 | 0.97 |

## Open-loop details

| rate | completions/s | generator lag p99 (ms) | bench CPU | api CPU max/mean | realtime CPU max | redis CPU max | postgres CPU max |
|---|---|---|---|---|---|---|---|
| 50/s | 50.0 | 4.62 | 15% | - / - | - | - | - |
| 100/s | 72.7 | 1054.9 | 56% | - / - | - | - | - |


