# Phase 3 latency benchmark: summary

Run: 2026-10-10 05:55:50 UTC on , 0 GiB RAM; Docker VM: .
All times in milliseconds. e2e = kitchen frame received minus (intended) POST send time, same host clock. Percentiles are nearest-rank over every order (no outlier trimming). `errors` = HTTP failures + frames never received; `late` = frames that took more than 2 s (still included in the percentiles).

## End-to-end: diner tap to kitchen receives (last client of the fan-out)

| Scenario | n | errors | mean | p50 | p90 | p95 | p99 | max | min |
|---|---|---|---|---|---|---|---|---|---|
| C open-loop 50/s | 1000 | 0 | 10.67 | 10.29 | 12.69 | 13.59 | 19.04 | 87.37 | 5.83 |
| C open-loop 100/s | 2000 | 0 | 9.44 | 7.07 | 8.83 | 11.15 | 93.66 | 208.2 | 3.96 |
| C open-loop 200/s | 4000 | 0 | 6.91 | 4.90 | 6.78 | 9.17 | 76.00 | 154.1 | 2.19 |

## POST round trip (what the diner's request takes)

| Scenario | n | errors | mean | p50 | p90 | p95 | p99 | max | min |
|---|---|---|---|---|---|---|---|---|---|
| C open-loop 50/s | 1000 | 0 | 10.62 | 10.22 | 12.69 | 13.56 | 19.27 | 90.95 | 5.84 |
| C open-loop 100/s | 2000 | 0 | 9.52 | 7.07 | 8.88 | 10.86 | 95.50 | 212.7 | 4.06 |
| C open-loop 200/s | 4000 | 0 | 6.91 | 4.74 | 6.65 | 9.40 | 78.84 | 155.9 | 2.13 |

## Publish to receive (envelope published_at_us to client frame; needs host/VM clocks to agree)

| Scenario | n | errors | mean | p50 | p90 | p95 | p99 | max | min |
|---|---|---|---|---|---|---|---|---|---|
| C open-loop 50/s | 1000 | 0 | 1.12 | 1.04 | 1.40 | 1.62 | 2.89 | 8.27 | 0.56 |
| C open-loop 100/s | 2000 | 0 | 0.93 | 0.83 | 1.18 | 1.50 | 2.68 | 14.54 | 0.40 |
| C open-loop 200/s | 4000 | 0 | 0.84 | 0.78 | 1.11 | 1.23 | 1.73 | 24.10 | 0.31 |

## Open-loop details

| rate | completions/s | generator lag p99 (ms) | bench CPU | api CPU max/mean | realtime CPU max | redis CPU max | postgres CPU max |
|---|---|---|---|---|---|---|---|
| 50/s | 50.0 | 5.68 | 8% | - / - | - | - | - |
| 100/s | 100.0 | 2.89 | 14% | - / - | - | - | - |
| 200/s | 199.4 | 2.24 | 21% | - / - | - | - | - |


## Server-side histograms (Prometheus, same window; bucket-interpolated quantiles)

| Scenario | metric | count | mean | p50 | p95 | p99 |
|---|---|---|---|---|---|---|
| C50 | order_create_seconds | 1020 | 3.66 | 3.80 | 4.97 | 8.40 |
| C50 | order_event_publish_seconds | 1020 | 0.25 | 0.20 | 0.49 | 0.98 |
| C50 | realtime_fanout_seconds | 1020 | 0.05 | 0.05 | 0.10 | 0.18 |
| C50 | realtime_publish_to_send_seconds | 10200 | 0.42 | 0.39 | 0.83 | 1.51 |
| C50 | http_post_orders_seconds | 1020 | 6.60 | 6.33 | 9.32 | 14.93 |
| C100 | order_create_seconds | 2020 | 3.82 | 2.48 | 4.89 | 45.96 |
| C100 | order_event_publish_seconds | 2020 | 0.26 | 0.18 | 0.43 | 3.04 |
| C100 | realtime_fanout_seconds | 2020 | 0.03 | 0.05 | 0.10 | 0.11 |
| C100 | realtime_publish_to_send_seconds | 20200 | 0.32 | 0.33 | 0.62 | 0.99 |
| C100 | http_post_orders_seconds | 2020 | 6.65 | 4.20 | 8.23 | 84.23 |
| C200 | order_create_seconds | 4020 | 3.04 | 1.61 | 4.24 | 53.78 |
| C200 | order_event_publish_seconds | 4020 | 0.21 | 0.17 | 0.45 | 2.74 |
| C200 | realtime_fanout_seconds | 4020 | 0.02 | 0.05 | 0.10 | 0.10 |
| C200 | realtime_publish_to_send_seconds | 40200 | 0.23 | 0.19 | 0.43 | 0.64 |
| C200 | http_post_orders_seconds | 4020 | 5.25 | 3.83 | 7.28 | 76.25 |

