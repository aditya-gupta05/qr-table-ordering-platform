# Phase 3 latency benchmark: summary

Run: 2026-10-10 05:47:53 UTC on , 0 GiB RAM; Docker VM: .
All times in milliseconds. e2e = kitchen frame received minus (intended) POST send time, same host clock. Percentiles are nearest-rank over every order (no outlier trimming). `errors` = HTTP failures + frames never received; `late` = frames that took more than 2 s (still included in the percentiles).

## End-to-end: diner tap to kitchen receives (last client of the fan-out)

| Scenario | n | errors | mean | p50 | p90 | p95 | p99 | max | min |
|---|---|---|---|---|---|---|---|---|---|
| A sequential, 1 client | 1000 | 0 | 3.32 | 3.17 | 3.96 | 4.40 | 5.72 | 14.48 | 2.42 |
| C open-loop 50/s | 1000 | 0 | 12.96 | 10.27 | 12.84 | 14.91 | 122.3 | 230.6 | 5.35 |
| C open-loop 100/s | 2000 | 0 | 7.09 | 6.63 | 7.84 | 8.37 | 17.55 | 93.18 | 3.64 |
| C open-loop 200/s | 4000 | 0 | 15.00 | 5.59 | 10.23 | 33.97 | 337.4 | 666.2 | 2.10 |

## POST round trip (what the diner's request takes)

| Scenario | n | errors | mean | p50 | p90 | p95 | p99 | max | min |
|---|---|---|---|---|---|---|---|---|---|
| A sequential, 1 client | 1000 | 0 | 3.48 | 3.34 | 4.13 | 4.59 | 5.80 | 14.31 | 2.61 |
| C open-loop 50/s | 1000 | 0 | 12.97 | 10.25 | 12.77 | 14.75 | 123.3 | 234.4 | 5.23 |
| C open-loop 100/s | 2000 | 0 | 7.06 | 6.60 | 7.83 | 8.29 | 18.18 | 93.82 | 3.74 |
| C open-loop 200/s | 4000 | 0 | 15.18 | 5.43 | 10.68 | 37.41 | 340.6 | 668.7 | 2.07 |

## Publish to receive (envelope published_at_us to client frame; needs host/VM clocks to agree)

| Scenario | n | errors | mean | p50 | p90 | p95 | p99 | max | min |
|---|---|---|---|---|---|---|---|---|---|
| A sequential, 1 client | 1000 | 0 | 0.38 | 0.37 | 0.56 | 0.63 | 0.80 | 4.79 | 0.17 |
| C open-loop 50/s | 1000 | 0 | 1.17 | 1.06 | 1.44 | 1.69 | 3.28 | 13.84 | 0.42 |
| C open-loop 100/s | 2000 | 0 | 0.84 | 0.79 | 1.00 | 1.08 | 1.53 | 20.51 | 0.35 |
| C open-loop 200/s | 4000 | 0 | 1.11 | 0.86 | 2.20 | 2.33 | 2.95 | 47.96 | 0.30 |

## Open-loop details

| rate | completions/s | generator lag p99 (ms) | bench CPU | api CPU max/mean | realtime CPU max | redis CPU max | postgres CPU max |
|---|---|---|---|---|---|---|---|
| 50/s | 50.0 | 6.16 | 9% | - / - | - | - | - |
| 100/s | 100.0 | 2.95 | 12% | - / - | - | - | - |
| 200/s | 200.0 | 8.26 | 29% | - / - | - | - | - |


## Server-side histograms (Prometheus, same window; bucket-interpolated quantiles)

| Scenario | metric | count | mean | p50 | p95 | p99 |
|---|---|---|---|---|---|---|
| A | order_create_seconds | 1050 | 1.64 | 1.56 | 2.64 | 4.07 |
| A | order_event_publish_seconds | 1050 | 0.12 | 0.14 | 0.24 | 0.42 |
| A | realtime_fanout_seconds | 1050 | 0.02 | 0.05 | 0.10 | 0.10 |
| A | realtime_publish_to_send_seconds | 1050 | 0.20 | 0.18 | 0.36 | 0.53 |
| A | http_post_orders_seconds | 1050 | 3.10 | 3.59 | 4.92 | 6.99 |
| C50 | order_create_seconds | 1020 | 4.84 | 3.61 | 5.97 | 68.00 |
| C50 | order_event_publish_seconds | 1020 | 0.30 | 0.20 | 0.50 | 2.24 |
| C50 | realtime_fanout_seconds | 1020 | 0.04 | 0.05 | 0.10 | 0.15 |
| C50 | realtime_publish_to_send_seconds | 10200 | 0.42 | 0.39 | 0.85 | 1.93 |
| C50 | http_post_orders_seconds | 1020 | 8.15 | 6.20 | 9.70 | 122.5 |
| C100 | order_create_seconds | 2020 | 2.43 | 2.27 | 3.00 | 7.00 |
| C100 | order_event_publish_seconds | 2020 | 0.17 | 0.18 | 0.25 | 0.73 |
| C100 | realtime_fanout_seconds | 2020 | 0.03 | 0.05 | 0.10 | 0.10 |
| C100 | realtime_publish_to_send_seconds | 20200 | 0.28 | 0.29 | 0.49 | 0.87 |
| C100 | http_post_orders_seconds | 2020 | 4.44 | 3.85 | 6.09 | 13.62 |
| C200 | order_create_seconds | 4020 | 7.30 | 1.65 | 17.70 | 215.5 |
| C200 | order_event_publish_seconds | 4020 | 0.35 | 0.17 | 1.69 | 4.59 |
| C200 | realtime_fanout_seconds | 4020 | 0.02 | 0.05 | 0.10 | 0.10 |
| C200 | realtime_publish_to_send_seconds | 40200 | 0.23 | 0.19 | 0.45 | 0.78 |
| C200 | http_post_orders_seconds | 4020 | 10.40 | 3.91 | 36.32 | 235.0 |

## Clock check (scenario A)

published_at minus send: p50 2.80 ms, min 2.18 ms; response minus published_at: p50 0.50 ms, min 0.36 ms; orders where either was negative: 0. A negative value would mean the Docker VM clock disagrees with the host clock.

## Latency budget (scenario A, means)

| Stage | mean ms |
|---|---|
| Client send to API `published_at`, excluding DB: network ingress (host to Docker port-forward), uvicorn parse, routing, post-commit reload, envelope build (= client mean of published_at minus t_send, minus the DB row below) | 1.31 |
| API pricing queries + INSERTs + COMMIT (`order_create_seconds`, Prometheus) | 1.64 |
| `published_at` to Go frame write (`realtime_publish_to_send_seconds`, Prometheus): Redis PUBLISH + hop + Go fan-out + queue + writer | 0.20 |
| Go WS write to client read (client pub_to_recv minus publish_to_send) | 0.18 |
| **Measured end-to-end (client, mean)** | 3.32 |

Reference points, not additive: 
- `order_event_publish_seconds` (API's Redis PUBLISH call): 0.12 ms
- `realtime_fanout_seconds` (Go lookup + enqueue): 0.02 ms
- Redis hop to Go subscriber (publish_to_send minus fanout, includes the PUBLISH): 0.18 ms
- API HTTP handler total (`http_request_duration_seconds`): 3.10 ms
- Client POST round trip: 3.48 ms; port-forward + client overhead = RTT minus handler = 0.38 ms (both directions)

Rows 1-4 sum to the measured end-to-end by construction (`published_at` is the pivot). Row 1 is a residual and is only meaningful if the host and Docker VM clocks agree; see the clock check. Server means are exact (Prometheus `_sum/_count` deltas), not bucket-interpolated. The HTTP handler total includes response serialisation, which happens after the publish and is not on the notification path.

