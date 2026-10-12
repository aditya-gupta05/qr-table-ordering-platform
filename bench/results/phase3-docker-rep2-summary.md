# Phase 3 latency benchmark: summary

Run: 2026-10-10 05:57:16 UTC on , 0 GiB RAM; Docker VM: .
All times in milliseconds. e2e = kitchen frame received minus (intended) POST send time, same host clock. Percentiles are nearest-rank over every order (no outlier trimming). `errors` = HTTP failures + frames never received; `late` = frames that took more than 2 s (still included in the percentiles).

## End-to-end: diner tap to kitchen receives (last client of the fan-out)

| Scenario | n | errors | mean | p50 | p90 | p95 | p99 | max | min |
|---|---|---|---|---|---|---|---|---|---|
| C open-loop 50/s | 1000 | 0 | 10.43 | 10.07 | 12.37 | 13.15 | 21.99 | 51.85 | 5.25 |
| C open-loop 100/s | 2000 | 0 | 7.07 | 6.59 | 8.09 | 8.53 | 12.88 | 83.22 | 3.53 |
| C open-loop 200/s | 4000 | 0 | 20.86 | 6.26 | 21.72 | 81.68 | 401.2 | 774.1 | 1.98 |

## POST round trip (what the diner's request takes)

| Scenario | n | errors | mean | p50 | p90 | p95 | p99 | max | min |
|---|---|---|---|---|---|---|---|---|---|
| C open-loop 50/s | 1000 | 0 | 10.38 | 10.00 | 12.36 | 13.20 | 22.22 | 52.07 | 5.06 |
| C open-loop 100/s | 2000 | 0 | 7.04 | 6.55 | 8.09 | 8.56 | 12.76 | 84.84 | 3.49 |
| C open-loop 200/s | 4000 | 0 | 21.21 | 5.94 | 22.87 | 85.06 | 405.1 | 778.7 | 2.20 |

## Publish to receive (envelope published_at_us to client frame; needs host/VM clocks to agree)

| Scenario | n | errors | mean | p50 | p90 | p95 | p99 | max | min |
|---|---|---|---|---|---|---|---|---|---|
| C open-loop 50/s | 1000 | 0 | 1.16 | 1.06 | 1.45 | 1.66 | 2.56 | 14.52 | 0.51 |
| C open-loop 100/s | 2000 | 0 | 0.84 | 0.79 | 1.03 | 1.15 | 1.67 | 5.61 | 0.33 |
| C open-loop 200/s | 4000 | 0 | 1.21 | 0.98 | 2.21 | 2.37 | 3.22 | 28.69 | 0.29 |

## Open-loop details

| rate | completions/s | generator lag p99 (ms) | bench CPU | api CPU max/mean | realtime CPU max | redis CPU max | postgres CPU max |
|---|---|---|---|---|---|---|---|
| 50/s | 50.0 | 5.68 | 8% | - / - | - | - | - |
| 100/s | 100.0 | 3.21 | 12% | - / - | - | - | - |
| 200/s | 200.0 | 11.27 | 35% | - / - | - | - | - |


## Server-side histograms (Prometheus, same window; bucket-interpolated quantiles)

| Scenario | metric | count | mean | p50 | p95 | p99 |
|---|---|---|---|---|---|---|
| C50 | order_create_seconds | 1020 | 3.56 | 3.72 | 4.97 | 9.00 |
| C50 | order_event_publish_seconds | 1020 | 0.25 | 0.20 | 0.49 | 0.95 |
| C50 | realtime_fanout_seconds | 1020 | 0.05 | 0.05 | 0.10 | 0.21 |
| C50 | realtime_publish_to_send_seconds | 10200 | 0.44 | 0.39 | 0.87 | 1.96 |
| C50 | http_post_orders_seconds | 1020 | 6.39 | 6.23 | 9.17 | 14.94 |
| C100 | order_create_seconds | 2020 | 2.40 | 2.33 | 3.08 | 6.09 |
| C100 | order_event_publish_seconds | 2020 | 0.17 | 0.18 | 0.26 | 0.64 |
| C100 | realtime_fanout_seconds | 2020 | 0.03 | 0.05 | 0.10 | 0.10 |
| C100 | realtime_publish_to_send_seconds | 20200 | 0.28 | 0.30 | 0.50 | 0.89 |
| C100 | http_post_orders_seconds | 2020 | 4.40 | 3.88 | 6.45 | 9.86 |
| C200 | order_create_seconds | 4020 | 12.35 | 1.71 | 46.60 | 324.4 |
| C200 | order_event_publish_seconds | 4020 | 0.45 | 0.18 | 2.97 | 5.62 |
| C200 | realtime_fanout_seconds | 4020 | 0.03 | 0.05 | 0.10 | 0.10 |
| C200 | realtime_publish_to_send_seconds | 40200 | 0.24 | 0.20 | 0.47 | 0.92 |
| C200 | http_post_orders_seconds | 4020 | 16.80 | 4.00 | 78.32 | 376.5 |

