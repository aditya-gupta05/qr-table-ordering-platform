# Phase 3 latency benchmark: summary

Run: 2026-10-11 02:57:04 UTC on , 0 GiB RAM; Docker VM: .
All times in milliseconds. e2e = kitchen frame received minus (intended) POST send time, same host clock. Percentiles are nearest-rank over every order (no outlier trimming). `errors` = HTTP failures + frames never received; `late` = frames that took more than 2 s (still included in the percentiles).

## End-to-end: diner tap to kitchen receives (last client of the fan-out)

| Scenario | n | errors | mean | p50 | p90 | p95 | p99 | max | min |
|---|---|---|---|---|---|---|---|---|---|
| C open-loop 100/s | 2000 | 0 | 7.89 | 6.47 | 8.29 | 11.11 | 55.50 | 104.5 | 3.75 |
| C open-loop 200/s | 4000 | 0 | 6.03 | 4.84 | 7.05 | 10.81 | 48.68 | 99.91 | 1.84 |

## POST round trip (what the diner's request takes)

| Scenario | n | errors | mean | p50 | p90 | p95 | p99 | max | min |
|---|---|---|---|---|---|---|---|---|---|
| C open-loop 100/s | 2000 | 0 | 7.91 | 6.45 | 8.26 | 10.89 | 55.59 | 108.5 | 3.66 |
| C open-loop 200/s | 4000 | 0 | 6.07 | 4.64 | 7.11 | 11.23 | 54.47 | 101.2 | 1.79 |

## Publish to receive (envelope published_at_us to client frame; needs host/VM clocks to agree)

| Scenario | n | errors | mean | p50 | p90 | p95 | p99 | max | min |
|---|---|---|---|---|---|---|---|---|---|
| C open-loop 100/s | 2000 | 0 | 0.97 | 0.85 | 1.17 | 1.42 | 2.86 | 43.77 | 0.36 |
| C open-loop 200/s | 4000 | 0 | 0.89 | 0.81 | 1.16 | 1.39 | 3.47 | 21.08 | 0.31 |

## Open-loop details

| rate | completions/s | generator lag p99 (ms) | bench CPU | api CPU max/mean | realtime CPU max | redis CPU max | postgres CPU max |
|---|---|---|---|---|---|---|---|
| 100/s | 100.0 | 3.91 | 14% | - / - | - | - | - |
| 200/s | 199.9 | 3.00 | 22% | - / - | - | - | - |


## Server-side histograms (Prometheus, same window; bucket-interpolated quantiles)

| Scenario | metric | count | mean | p50 | p95 | p99 |
|---|---|---|---|---|---|---|
| C100 | order_create_seconds | 2020 | 2.65 | 1.97 | 4.51 | 21.80 |
| C100 | order_event_publish_seconds | 2020 | 0.20 | 0.18 | 0.42 | 1.36 |
| C100 | realtime_fanout_seconds | 2020 | 0.03 | 0.05 | 0.10 | 0.14 |
| C100 | realtime_publish_to_send_seconds | 20200 | 0.32 | 0.31 | 0.62 | 1.56 |
| C100 | http_post_orders_seconds | 2020 | 4.84 | 3.92 | 7.48 | 37.08 |
| C200 | order_create_seconds | 4020 | 2.35 | 1.65 | 4.37 | 22.51 |
| C200 | order_event_publish_seconds | 4020 | 0.22 | 0.16 | 0.43 | 3.02 |
| C200 | realtime_fanout_seconds | 4020 | 0.02 | 0.05 | 0.10 | 0.10 |
| C200 | realtime_publish_to_send_seconds | 40200 | 0.27 | 0.21 | 0.49 | 1.42 |
| C200 | http_post_orders_seconds | 4020 | 4.31 | 3.56 | 7.81 | 41.25 |

