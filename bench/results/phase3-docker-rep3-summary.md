# Phase 3 latency benchmark: summary

Run: 2026-10-10 05:58:40 UTC on , 0 GiB RAM; Docker VM: .
All times in milliseconds. e2e = kitchen frame received minus (intended) POST send time, same host clock. Percentiles are nearest-rank over every order (no outlier trimming). `errors` = HTTP failures + frames never received; `late` = frames that took more than 2 s (still included in the percentiles).

## End-to-end: diner tap to kitchen receives (last client of the fan-out)

| Scenario | n | errors | mean | p50 | p90 | p95 | p99 | max | min |
|---|---|---|---|---|---|---|---|---|---|
| C open-loop 50/s | 1000 | 0 | 10.11 | 9.95 | 12.29 | 13.15 | 15.75 | 25.39 | 4.94 |
| C open-loop 100/s | 2000 | 0 | 12.17 | 6.74 | 8.49 | 11.52 | 217.3 | 426.3 | 3.66 |
| C open-loop 200/s | 4000 | 0 | 14.31 | 5.33 | 8.69 | 54.04 | 270.1 | 582.8 | 2.19 |

## POST round trip (what the diner's request takes)

| Scenario | n | errors | mean | p50 | p90 | p95 | p99 | max | min |
|---|---|---|---|---|---|---|---|---|---|
| C open-loop 50/s | 1000 | 0 | 10.04 | 9.89 | 12.26 | 13.06 | 15.73 | 26.87 | 4.82 |
| C open-loop 100/s | 2000 | 0 | 12.28 | 6.73 | 8.51 | 11.09 | 237.3 | 429.3 | 3.59 |
| C open-loop 200/s | 4000 | 0 | 14.49 | 5.14 | 9.22 | 57.04 | 278.9 | 589.9 | 2.16 |

## Publish to receive (envelope published_at_us to client frame; needs host/VM clocks to agree)

| Scenario | n | errors | mean | p50 | p90 | p95 | p99 | max | min |
|---|---|---|---|---|---|---|---|---|---|
| C open-loop 50/s | 1000 | 0 | 1.15 | 1.07 | 1.43 | 1.65 | 2.28 | 11.47 | 0.50 |
| C open-loop 100/s | 2000 | 0 | 0.93 | 0.84 | 1.15 | 1.29 | 2.29 | 25.90 | 0.37 |
| C open-loop 200/s | 4000 | 0 | 0.97 | 0.83 | 1.50 | 1.96 | 2.55 | 19.33 | 0.28 |

## Open-loop details

| rate | completions/s | generator lag p99 (ms) | bench CPU | api CPU max/mean | realtime CPU max | redis CPU max | postgres CPU max |
|---|---|---|---|---|---|---|---|
| 50/s | 50.0 | 5.66 | 8% | - / - | - | - | - |
| 100/s | 100.0 | 3.85 | 15% | - / - | - | - | - |
| 200/s | 199.9 | 2.95 | 25% | - / - | - | - | - |


## Server-side histograms (Prometheus, same window; bucket-interpolated quantiles)

| Scenario | metric | count | mean | p50 | p95 | p99 |
|---|---|---|---|---|---|---|
| C50 | order_create_seconds | 1020 | 3.46 | 3.74 | 4.97 | 7.24 |
| C50 | order_event_publish_seconds | 1020 | 0.24 | 0.20 | 0.47 | 0.87 |
| C50 | realtime_fanout_seconds | 1020 | 0.05 | 0.05 | 0.10 | 0.16 |
| C50 | realtime_publish_to_send_seconds | 10200 | 0.43 | 0.39 | 0.84 | 1.44 |
| C50 | http_post_orders_seconds | 1020 | 6.23 | 6.22 | 8.77 | 12.60 |
| C100 | order_create_seconds | 2020 | 5.42 | 2.29 | 4.92 | 131.3 |
| C100 | order_event_publish_seconds | 2020 | 0.26 | 0.18 | 0.45 | 3.09 |
| C100 | realtime_fanout_seconds | 2020 | 0.03 | 0.05 | 0.10 | 0.10 |
| C100 | realtime_publish_to_send_seconds | 20200 | 0.29 | 0.27 | 0.50 | 0.96 |
| C100 | http_post_orders_seconds | 2020 | 8.22 | 3.92 | 8.25 | 177.1 |
| C200 | order_create_seconds | 4020 | 9.62 | 1.64 | 32.61 | 267.1 |
| C200 | order_event_publish_seconds | 4020 | 0.36 | 0.17 | 2.00 | 4.94 |
| C200 | realtime_fanout_seconds | 4020 | 0.02 | 0.05 | 0.10 | 0.10 |
| C200 | realtime_publish_to_send_seconds | 40200 | 0.24 | 0.19 | 0.44 | 0.73 |
| C200 | http_post_orders_seconds | 4020 | 12.65 | 3.90 | 53.62 | 278.6 |

