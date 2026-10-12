# Phase 3 latency benchmark: summary

Run: 2026-10-09 22:45:36 PDT on Apple M2 Pro, 16 GiB RAM; Docker VM: 10 CPUs, 8217382912 bytes, Docker Desktop, 29.2.0.
All times in milliseconds. e2e = kitchen frame received minus (intended) POST send time, same host clock. Percentiles are nearest-rank over every order (no outlier trimming). `errors` = HTTP failures + frames never received; `late` = frames that took more than 2 s (still included in the percentiles).

## End-to-end: diner tap to kitchen receives (last client of the fan-out)

| Scenario | n | errors | mean | p50 | p90 | p95 | p99 | max | min |
|---|---|---|---|---|---|---|---|---|---|
| C open-loop 100/s | 2000 | 0 | 8.06 | 7.60 | 9.98 | 11.52 | 14.90 | 26.97 | 4.82 |
| C open-loop 200/s (late 1368) | 3999 | 1 | 3733.7 | 42.68 | 14109.1 | 16521.8 | 20236.2 | 21875.6 | 4.49 |

## POST round trip (what the diner's request takes)

| Scenario | n | errors | mean | p50 | p90 | p95 | p99 | max | min |
|---|---|---|---|---|---|---|---|---|---|
| C open-loop 100/s | 2000 | 0 | 8.11 | 7.65 | 10.10 | 11.63 | 15.22 | 27.76 | 4.92 |
| C open-loop 200/s (late 1368) | 3999 | 1 | 3781.2 | 46.22 | 14167.9 | 16616.8 | 20299.1 | 21887.2 | 4.54 |

## Publish to receive (envelope published_at_us to client frame; needs host/VM clocks to agree)

| Scenario | n | errors | mean | p50 | p90 | p95 | p99 | max | min |
|---|---|---|---|---|---|---|---|---|---|
| C open-loop 100/s | 2000 | 0 | 5.28 | 5.05 | 6.26 | 6.80 | 9.29 | 14.79 | 3.74 |
| C open-loop 200/s (late 1368) | 3999 | 1 | 28.51 | 2.75 | 81.33 | 132.7 | 276.3 | 372.6 | 0.89 |

## Open-loop details

| rate | completions/s | generator lag p99 (ms) | bench CPU | api CPU max/mean | realtime CPU max | redis CPU max | postgres CPU max |
|---|---|---|---|---|---|---|---|
| 100/s | 100.0 | 3.65 | 26% | 48% / 37% | 5% | 2% | 12% |
| 200/s | 114.1 | 389.9 | 59% | 85% / 39% | 8% | 3% | 47% |

- 100/s mean Postgres backends by state/wait: idle/Client=4.7, idle in transaction/Client=0.2, active/-=0.1, active/IO=0.0, idle in transaction/-=0.0
- 200/s mean Postgres backends by state/wait: idle/Client=4.3, idle in transaction/Client=1.4, active/Client=0.1, active/-=0.1, active/IO=0.0, idle/-=0.0, idle in transaction/-=0.0

## Server-side histograms (Prometheus, same window; bucket-interpolated quantiles)

| Scenario | metric | count | mean | p50 | p95 | p99 |
|---|---|---|---|---|---|---|
| C100 | order_create_seconds | 2022 | 2.41 | 2.46 | 3.65 | 5.00 |
| C100 | order_event_publish_seconds | 2022 | 0.17 | 0.18 | 0.25 | 0.60 |
| C100 | realtime_fanout_seconds | 2022 | 0.03 | 0.05 | 0.10 | 0.10 |
| C100 | realtime_publish_to_send_seconds | 20202 | 0.28 | 0.30 | 0.49 | 0.91 |
| C100 | http_post_orders_seconds | 2022 | 4.55 | 3.95 | 6.88 | 9.53 |
| C200 | order_create_seconds | 4019 | 23.02 | 2.63 | 150.5 | 354.7 |
| C200 | order_event_publish_seconds | 4019 | 0.85 | 0.22 | 4.27 | 7.06 |
| C200 | realtime_fanout_seconds | 4019 | 0.03 | 0.05 | 0.10 | 0.10 |
| C200 | realtime_publish_to_send_seconds | 40190 | 0.29 | 0.23 | 0.49 | 0.95 |
| C200 | http_post_orders_seconds | 4019 | 40.31 | 11.58 | 208.4 | 405.0 |

