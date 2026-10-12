# Phase 3 latency benchmark: summary

Run: 2026-10-10 06:01:59 UTC on , 0 GiB RAM; Docker VM: .
All times in milliseconds. e2e = kitchen frame received minus (intended) POST send time, same host clock. Percentiles are nearest-rank over every order (no outlier trimming). `errors` = HTTP failures + frames never received; `late` = frames that took more than 2 s (still included in the percentiles).

## End-to-end: diner tap to kitchen receives (last client of the fan-out)

| Scenario | n | errors | mean | p50 | p90 | p95 | p99 | max | min |
|---|---|---|---|---|---|---|---|---|---|
| C open-loop 200/s | 4000 | 0 | 9.06 | 4.60 | 6.88 | 12.09 | 195.2 | 406.4 | 1.95 |

## POST round trip (what the diner's request takes)

| Scenario | n | errors | mean | p50 | p90 | p95 | p99 | max | min |
|---|---|---|---|---|---|---|---|---|---|
| C open-loop 200/s | 4000 | 0 | 9.07 | 4.46 | 6.66 | 12.52 | 201.0 | 409.1 | 1.91 |

## Publish to receive (envelope published_at_us to client frame; needs host/VM clocks to agree)

| Scenario | n | errors | mean | p50 | p90 | p95 | p99 | max | min |
|---|---|---|---|---|---|---|---|---|---|
| C open-loop 200/s | 4000 | 0 | 0.86 | 0.77 | 1.48 | 1.62 | 2.08 | 29.77 | 0.31 |

## Open-loop details

| rate | completions/s | generator lag p99 (ms) | bench CPU | api CPU max/mean | realtime CPU max | redis CPU max | postgres CPU max |
|---|---|---|---|---|---|---|---|
| 200/s | 200.0 | 4.76 | 23% | - / - | - | - | - |


## Server-side histograms (Prometheus, same window; bucket-interpolated quantiles)

| Scenario | metric | count | mean | p50 | p95 | p99 |
|---|---|---|---|---|---|---|
| C200 | order_create_seconds | 4020 | 4.37 | 1.56 | 4.57 | 118.0 |
| C200 | order_event_publish_seconds | 4020 | 0.22 | 0.09 | 0.48 | 4.01 |
| C200 | realtime_fanout_seconds | 4020 | 0.02 | 0.05 | 0.10 | 0.10 |
| C200 | realtime_publish_to_send_seconds | 40200 | 0.20 | 0.18 | 0.38 | 0.70 |
| C200 | http_post_orders_seconds | 4020 | 6.56 | 3.46 | 9.07 | 158.2 |

