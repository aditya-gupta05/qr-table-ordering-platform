# Phase 3 latency benchmark: summary

Run: 2026-10-09 22:50:25 PDT on Apple M2 Pro, 16 GiB RAM; Docker VM: 10 CPUs, 8217382912 bytes, Docker Desktop, 29.2.0.
All times in milliseconds. e2e = kitchen frame received minus (intended) POST send time, same host clock. Percentiles are nearest-rank over every order (no outlier trimming). `errors` = HTTP failures + frames never received; `late` = frames that took more than 2 s (still included in the percentiles).

## End-to-end: diner tap to kitchen receives (last client of the fan-out)

| Scenario | n | errors | mean | p50 | p90 | p95 | p99 | max | min |
|---|---|---|---|---|---|---|---|---|---|
| C open-loop 50/s | 1000 | 0 | 52.82 | 11.86 | 70.70 | 377.1 | 777.3 | 933.1 | 5.75 |
| C open-loop 100/s (late 828) | 1999 | 1 | 2308.9 | 1374.4 | 5989.3 | 7358.2 | 10612.4 | 13546.6 | 5.16 |

## POST round trip (what the diner's request takes)

| Scenario | n | errors | mean | p50 | p90 | p95 | p99 | max | min |
|---|---|---|---|---|---|---|---|---|---|
| C open-loop 50/s | 1000 | 0 | 53.71 | 12.04 | 72.00 | 386.7 | 787.4 | 940.9 | 5.60 |
| C open-loop 100/s (late 828) | 1999 | 1 | 2356.3 | 1431.7 | 6014.1 | 7391.9 | 10614.8 | 13738.6 | 5.85 |

## Publish to receive (envelope published_at_us to client frame; needs host/VM clocks to agree)

| Scenario | n | errors | mean | p50 | p90 | p95 | p99 | max | min |
|---|---|---|---|---|---|---|---|---|---|
| C open-loop 50/s | 1000 | 0 | 3.54 | 3.17 | 4.98 | 6.07 | 10.78 | 31.22 | 1.33 |
| C open-loop 100/s (late 828) | 1999 | 1 | 43.99 | 7.63 | 134.2 | 203.0 | 336.6 | 676.3 | 1.19 |

## Open-loop details

| rate | completions/s | generator lag p99 (ms) | bench CPU | api CPU max/mean | realtime CPU max | redis CPU max | postgres CPU max |
|---|---|---|---|---|---|---|---|
| 50/s | 49.5 | 29.71 | 18% | - / - | - | - | - |
| 100/s | 83.1 | 650.9 | 57% | - / - | - | - | - |


