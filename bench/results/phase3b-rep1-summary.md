# Phase 3 latency benchmark: summary

Run: 2026-10-10 19:52:41 PDT on Apple M2 Pro, 16 GiB RAM; Docker VM: 10 CPUs, 8217382912 bytes, Docker Desktop, 29.2.0.
All times in milliseconds. e2e = kitchen frame received minus (intended) POST send time, same host clock. Percentiles are nearest-rank over every order (no outlier trimming). `errors` = HTTP failures + frames never received; `late` = frames that took more than 2 s (still included in the percentiles).

## End-to-end: diner tap to kitchen receives (last client of the fan-out)

| Scenario | n | errors | mean | p50 | p90 | p95 | p99 | max | min |
|---|---|---|---|---|---|---|---|---|---|
| C open-loop 50/s | 1000 | 0 | 14.01 | 10.41 | 14.88 | 22.25 | 113.7 | 192.0 | 6.25 |
| C open-loop 100/s | 2000 | 0 | 27.44 | 7.55 | 55.29 | 137.7 | 384.5 | 645.3 | 4.54 |
| C open-loop 200/s | 4000 | 0 | 5.91 | 5.61 | 7.33 | 7.97 | 10.34 | 68.01 | 3.49 |

## POST round trip (what the diner's request takes)

| Scenario | n | errors | mean | p50 | p90 | p95 | p99 | max | min |
|---|---|---|---|---|---|---|---|---|---|
| C open-loop 50/s | 1000 | 0 | 14.20 | 10.51 | 15.03 | 23.43 | 118.5 | 198.1 | 6.13 |
| C open-loop 100/s | 2000 | 0 | 28.31 | 7.77 | 59.79 | 143.1 | 387.9 | 654.9 | 4.35 |
| C open-loop 200/s | 4000 | 0 | 5.95 | 5.58 | 7.39 | 8.13 | 10.24 | 70.60 | 3.39 |

## Publish to receive (envelope published_at_us to client frame; needs host/VM clocks to agree)

| Scenario | n | errors | mean | p50 | p90 | p95 | p99 | max | min |
|---|---|---|---|---|---|---|---|---|---|
| C open-loop 50/s | 1000 | 0 | 3.25 | 2.88 | 4.29 | 4.99 | 9.26 | 86.66 | 1.36 |
| C open-loop 100/s | 2000 | 0 | 4.06 | 3.13 | 5.50 | 8.09 | 18.65 | 121.5 | 1.87 |
| C open-loop 200/s | 4000 | 0 | 1.98 | 1.75 | 2.85 | 3.20 | 4.02 | 8.98 | 0.99 |

## Open-loop details

| rate | completions/s | generator lag p99 (ms) | bench CPU | api CPU max/mean | realtime CPU max | redis CPU max | postgres CPU max |
|---|---|---|---|---|---|---|---|
| 50/s | 50.0 | 3.40 | 16% | - / - | - | - | - |
| 100/s | 100.0 | 114.5 | 25% | - / - | - | - | - |
| 200/s | 200.0 | 2.14 | 30% | - / - | - | - | - |


## Server-side histograms (Prometheus, same window; bucket-interpolated quantiles)

| Scenario | metric | count | mean | p50 | p95 | p99 |
|---|---|---|---|---|---|---|
| C50 | order_create_seconds | 1020 | 4.92 | 3.52 | 8.33 | 61.67 |
| C50 | order_event_publish_seconds | 1020 | 0.41 | 0.22 | 0.83 | 4.93 |
| C50 | realtime_fanout_seconds | 1020 | 0.09 | 0.05 | 0.10 | 0.23 |
| C50 | realtime_publish_to_send_seconds | 10200 | 0.55 | 0.41 | 0.98 | 2.46 |
| C50 | http_post_orders_seconds | 1020 | 8.73 | 6.13 | 16.76 | 110.9 |
| C100 | order_create_seconds | 2020 | 8.71 | 2.57 | 32.55 | 187.3 |
| C100 | order_event_publish_seconds | 2020 | 0.86 | 0.20 | 2.66 | 13.56 |
| C100 | realtime_fanout_seconds | 2020 | 0.04 | 0.05 | 0.10 | 0.24 |
| C100 | realtime_publish_to_send_seconds | 20200 | 0.61 | 0.38 | 1.33 | 4.31 |
| C100 | http_post_orders_seconds | 2020 | 14.80 | 4.43 | 75.68 | 244.9 |
| C200 | order_create_seconds | 4020 | 1.87 | 1.67 | 2.87 | 4.52 |
| C200 | order_event_publish_seconds | 4020 | 0.15 | 0.17 | 0.25 | 0.48 |
| C200 | realtime_fanout_seconds | 4020 | 0.02 | 0.05 | 0.10 | 0.10 |
| C200 | realtime_publish_to_send_seconds | 40200 | 0.24 | 0.20 | 0.47 | 0.75 |
| C200 | http_post_orders_seconds | 4020 | 3.59 | 3.78 | 4.96 | 7.42 |

