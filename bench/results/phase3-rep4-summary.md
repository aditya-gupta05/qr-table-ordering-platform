# Phase 3 latency benchmark: summary

Run: 2026-10-09 22:51:56 PDT on Apple M2 Pro, 16 GiB RAM; Docker VM: 10 CPUs, 8217382912 bytes, Docker Desktop, 29.2.0.
All times in milliseconds. e2e = kitchen frame received minus (intended) POST send time, same host clock. Percentiles are nearest-rank over every order (no outlier trimming). `errors` = HTTP failures + frames never received; `late` = frames that took more than 2 s (still included in the percentiles).

## End-to-end: diner tap to kitchen receives (last client of the fan-out)

| Scenario | n | errors | mean | p50 | p90 | p95 | p99 | max | min |
|---|---|---|---|---|---|---|---|---|---|
| C open-loop 50/s | 1000 | 0 | 13.43 | 10.59 | 13.62 | 17.98 | 116.8 | 177.9 | 6.33 |
| C open-loop 100/s | 2000 | 0 | 8.03 | 7.39 | 9.37 | 10.78 | 26.27 | 63.99 | 4.82 |

## POST round trip (what the diner's request takes)

| Scenario | n | errors | mean | p50 | p90 | p95 | p99 | max | min |
|---|---|---|---|---|---|---|---|---|---|
| C open-loop 50/s | 1000 | 0 | 13.54 | 10.59 | 13.63 | 18.05 | 122.9 | 178.2 | 6.43 |
| C open-loop 100/s | 2000 | 0 | 8.08 | 7.44 | 9.48 | 11.02 | 25.80 | 65.39 | 5.00 |

## Publish to receive (envelope published_at_us to client frame; needs host/VM clocks to agree)

| Scenario | n | errors | mean | p50 | p90 | p95 | p99 | max | min |
|---|---|---|---|---|---|---|---|---|---|
| C open-loop 50/s | 1000 | 0 | 2.86 | 2.74 | 3.85 | 4.27 | 5.30 | 12.36 | 1.21 |
| C open-loop 100/s | 2000 | 0 | 2.59 | 2.54 | 3.37 | 3.82 | 5.15 | 18.10 | 1.22 |

## Open-loop details

| rate | completions/s | generator lag p99 (ms) | bench CPU | api CPU max/mean | realtime CPU max | redis CPU max | postgres CPU max |
|---|---|---|---|---|---|---|---|
| 50/s | 50.0 | 3.42 | 15% | - / - | - | - | - |
| 100/s | 100.0 | 2.01 | 25% | - / - | - | - | - |


