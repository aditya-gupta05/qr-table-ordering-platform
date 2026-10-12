# Phase 3 latency benchmark: summary

Run: 2026-10-09 22:49:42 PDT on Apple M2 Pro, 16 GiB RAM; Docker VM: 10 CPUs, 8217382912 bytes, Docker Desktop, 29.2.0.
All times in milliseconds. e2e = kitchen frame received minus (intended) POST send time, same host clock. Percentiles are nearest-rank over every order (no outlier trimming). `errors` = HTTP failures + frames never received; `late` = frames that took more than 2 s (still included in the percentiles).

## End-to-end: diner tap to kitchen receives (last client of the fan-out)

| Scenario | n | errors | mean | p50 | p90 | p95 | p99 | max | min |
|---|---|---|---|---|---|---|---|---|---|
| C open-loop 50/s | 1000 | 0 | 13.17 | 10.91 | 14.45 | 16.99 | 102.3 | 190.6 | 6.08 |
| C open-loop 100/s | 2000 | 0 | 11.55 | 7.89 | 11.52 | 14.04 | 163.8 | 314.8 | 5.07 |

## POST round trip (what the diner's request takes)

| Scenario | n | errors | mean | p50 | p90 | p95 | p99 | max | min |
|---|---|---|---|---|---|---|---|---|---|
| C open-loop 50/s | 1000 | 0 | 13.25 | 10.89 | 14.60 | 17.37 | 112.2 | 193.0 | 6.05 |
| C open-loop 100/s | 2000 | 0 | 11.79 | 8.00 | 11.56 | 14.49 | 169.9 | 319.5 | 4.98 |

## Publish to receive (envelope published_at_us to client frame; needs host/VM clocks to agree)

| Scenario | n | errors | mean | p50 | p90 | p95 | p99 | max | min |
|---|---|---|---|---|---|---|---|---|---|
| C open-loop 50/s | 1000 | 0 | 3.50 | 3.29 | 4.66 | 5.38 | 8.65 | 28.21 | 1.62 |
| C open-loop 100/s | 2000 | 0 | 2.84 | 2.59 | 3.95 | 4.57 | 6.71 | 26.98 | 1.21 |

## Open-loop details

| rate | completions/s | generator lag p99 (ms) | bench CPU | api CPU max/mean | realtime CPU max | redis CPU max | postgres CPU max |
|---|---|---|---|---|---|---|---|
| 50/s | 50.0 | 4.24 | 16% | - / - | - | - | - |
| 100/s | 100.0 | 4.43 | 26% | - / - | - | - | - |


