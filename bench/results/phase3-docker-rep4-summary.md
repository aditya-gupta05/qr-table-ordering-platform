# Phase 3 latency benchmark: summary

Run: 2026-10-10 06:00:05 UTC on , 0 GiB RAM; Docker VM: .
All times in milliseconds. e2e = kitchen frame received minus (intended) POST send time, same host clock. Percentiles are nearest-rank over every order (no outlier trimming). `errors` = HTTP failures + frames never received; `late` = frames that took more than 2 s (still included in the percentiles).

## End-to-end: diner tap to kitchen receives (last client of the fan-out)

| Scenario | n | errors | mean | p50 | p90 | p95 | p99 | max | min |
|---|---|---|---|---|---|---|---|---|---|
| C open-loop 50/s | 1000 | 0 | 10.14 | 9.74 | 12.28 | 13.35 | 23.75 | 38.57 | 5.50 |
| C open-loop 100/s | 2000 | 0 | 6.65 | 6.53 | 7.81 | 8.30 | 9.54 | 25.38 | 3.46 |
| C open-loop 200/s (late 601) | 4000 | 0 | 762.3 | 5.76 | 3128.3 | 4311.5 | 6965.0 | 9105.3 | 2.14 |

## POST round trip (what the diner's request takes)

| Scenario | n | errors | mean | p50 | p90 | p95 | p99 | max | min |
|---|---|---|---|---|---|---|---|---|---|
| C open-loop 50/s | 1000 | 0 | 10.09 | 9.68 | 12.25 | 13.29 | 23.87 | 40.02 | 5.62 |
| C open-loop 100/s | 2000 | 0 | 6.61 | 6.49 | 7.79 | 8.29 | 9.58 | 25.68 | 3.34 |
| C open-loop 200/s (late 601) | 4000 | 0 | 777.5 | 5.64 | 3174.7 | 4347.3 | 6967.9 | 9106.7 | 2.11 |

## Publish to receive (envelope published_at_us to client frame; needs host/VM clocks to agree)

| Scenario | n | errors | mean | p50 | p90 | p95 | p99 | max | min |
|---|---|---|---|---|---|---|---|---|---|
| C open-loop 50/s | 1000 | 0 | 1.16 | 1.06 | 1.47 | 1.79 | 2.82 | 6.97 | 0.51 |
| C open-loop 100/s | 2000 | 0 | 0.81 | 0.77 | 1.00 | 1.09 | 1.71 | 4.31 | 0.39 |
| C open-loop 200/s (late 601) | 4000 | 0 | 8.94 | 0.86 | 31.78 | 40.43 | 56.38 | 69.16 | 0.27 |

## Open-loop details

| rate | completions/s | generator lag p99 (ms) | bench CPU | api CPU max/mean | realtime CPU max | redis CPU max | postgres CPU max |
|---|---|---|---|---|---|---|---|
| 50/s | 50.0 | 5.40 | 8% | - / - | - | - | - |
| 100/s | 100.0 | 3.39 | 12% | - / - | - | - | - |
| 200/s | 171.0 | 71.89 | 55% | - / - | - | - | - |


## Server-side histograms (Prometheus, same window; bucket-interpolated quantiles)

| Scenario | metric | count | mean | p50 | p95 | p99 |
|---|---|---|---|---|---|---|
| C50 | order_create_seconds | 1020 | 3.52 | 3.68 | 4.98 | 9.94 |
| C50 | order_event_publish_seconds | 1020 | 0.24 | 0.20 | 0.48 | 0.98 |
| C50 | realtime_fanout_seconds | 1020 | 0.05 | 0.05 | 0.10 | 0.18 |
| C50 | realtime_publish_to_send_seconds | 10200 | 0.42 | 0.39 | 0.85 | 1.33 |
| C50 | http_post_orders_seconds | 1020 | 6.35 | 6.24 | 9.25 | 19.83 |
| C100 | order_create_seconds | 2020 | 2.19 | 2.28 | 2.97 | 4.50 |
| C100 | order_event_publish_seconds | 2020 | 0.15 | 0.18 | 0.25 | 0.46 |
| C100 | realtime_fanout_seconds | 2020 | 0.03 | 0.05 | 0.10 | 0.10 |
| C100 | realtime_publish_to_send_seconds | 20200 | 0.27 | 0.28 | 0.49 | 0.86 |
| C100 | http_post_orders_seconds | 2020 | 4.11 | 3.83 | 5.48 | 7.33 |
| C200 | order_create_seconds | 4020 | 13.34 | 1.76 | 70.91 | 331.9 |
| C200 | order_event_publish_seconds | 4020 | 0.42 | 0.18 | 2.80 | 4.70 |
| C200 | realtime_fanout_seconds | 4020 | 0.02 | 0.05 | 0.10 | 0.10 |
| C200 | realtime_publish_to_send_seconds | 40200 | 0.24 | 0.19 | 0.44 | 0.73 |
| C200 | http_post_orders_seconds | 4020 | 20.94 | 4.54 | 91.09 | 352.8 |

