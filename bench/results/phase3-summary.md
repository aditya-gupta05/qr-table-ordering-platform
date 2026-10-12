# Phase 3 latency benchmark: summary

Run: 2026-10-09 22:38:49 PDT on Apple M2 Pro, 16 GiB RAM; Docker VM: 10 CPUs, 8217382912 bytes, Docker Desktop, 29.2.0.
All times in milliseconds. e2e = kitchen frame received minus (intended) POST send time, same host clock. Percentiles are nearest-rank over every order (no outlier trimming). `errors` = HTTP failures + frames never received; `late` = frames that took more than 2 s (still included in the percentiles).

## End-to-end: diner tap to kitchen receives (last client of the fan-out)

| Scenario | n | errors | mean | p50 | p90 | p95 | p99 | max | min |
|---|---|---|---|---|---|---|---|---|---|
| A sequential, 1 client | 1000 | 0 | 5.39 | 4.94 | 6.41 | 7.28 | 15.18 | 51.66 | 3.32 |
| B fan-out, 1 clients | 300 | 0 | 9.67 | 5.77 | 15.33 | 23.08 | 55.06 | 257.3 | 3.80 |
| B fan-out, 10 clients | 300 | 0 | 6.18 | 5.82 | 8.04 | 8.99 | 12.41 | 18.83 | 3.96 |
| B fan-out, 100 clients | 300 | 0 | 6.85 | 6.60 | 8.00 | 8.82 | 10.71 | 11.68 | 5.49 |
| C open-loop 10/s | 200 | 0 | 23.15 | 23.62 | 30.11 | 33.18 | 60.39 | 84.69 | 8.51 |
| C open-loop 50/s | 1000 | 0 | 12.56 | 11.25 | 14.70 | 17.04 | 46.44 | 105.0 | 6.25 |
| C open-loop 100/s | 2000 | 0 | 12.63 | 7.65 | 10.49 | 13.07 | 232.5 | 387.6 | 5.13 |
| C open-loop 200/s (late 214) | 4000 | 0 | 287.6 | 6.65 | 364.0 | 2383.0 | 5286.8 | 5885.7 | 3.84 |
| D status path | 300 | 0 | 10.11 | 9.09 | 12.51 | 14.02 | 28.80 | 98.12 | 4.57 |

## Fan-out: median client vs last client

| Scenario | n | errors | mean | p50 | p90 | p95 | p99 | max | min |
|---|---|---|---|---|---|---|---|---|---|
| B 1 clients, median client | 300 | 0 | 9.67 | 5.77 | 15.33 | 23.08 | 55.06 | 257.3 | 3.80 |
| B 1 clients, last client | 300 | 0 | 9.67 | 5.77 | 15.33 | 23.08 | 55.06 | 257.3 | 3.80 |
| B 10 clients, median client | 300 | 0 | 6.02 | 5.63 | 7.70 | 8.90 | 12.35 | 18.25 | 3.70 |
| B 10 clients, last client | 300 | 0 | 6.18 | 5.82 | 8.04 | 8.99 | 12.41 | 18.83 | 3.96 |
| B 100 clients, median client | 300 | 0 | 6.33 | 6.09 | 7.38 | 8.04 | 10.31 | 11.33 | 4.83 |
| B 100 clients, last client | 300 | 0 | 6.85 | 6.60 | 8.00 | 8.82 | 10.71 | 11.68 | 5.49 |

## POST round trip (what the diner's request takes)

| Scenario | n | errors | mean | p50 | p90 | p95 | p99 | max | min |
|---|---|---|---|---|---|---|---|---|---|
| A sequential, 1 client | 1000 | 0 | 5.89 | 5.35 | 6.99 | 7.97 | 15.98 | 53.48 | 3.75 |
| B fan-out, 1 clients | 300 | 0 | 10.05 | 6.29 | 15.36 | 22.89 | 59.70 | 258.4 | 4.12 |
| B fan-out, 10 clients | 300 | 0 | 6.17 | 5.73 | 8.01 | 9.20 | 12.70 | 18.72 | 3.98 |
| B fan-out, 100 clients | 300 | 0 | 5.34 | 5.04 | 6.58 | 7.79 | 9.86 | 10.44 | 3.86 |
| C open-loop 10/s | 200 | 0 | 23.23 | 23.88 | 30.54 | 33.16 | 60.68 | 85.00 | 8.70 |
| C open-loop 50/s | 1000 | 0 | 12.63 | 11.30 | 14.92 | 17.07 | 47.12 | 106.2 | 6.00 |
| C open-loop 100/s | 2000 | 0 | 12.78 | 7.68 | 10.59 | 13.21 | 241.5 | 391.8 | 5.12 |
| C open-loop 200/s (late 214) | 4000 | 0 | 294.6 | 6.83 | 378.7 | 2485.8 | 5292.3 | 5890.1 | 3.72 |
| D status path | 300 | 0 | 10.79 | 9.75 | 13.22 | 14.98 | 30.93 | 98.06 | 5.42 |

## Publish to receive (envelope published_at_us to client frame; needs host/VM clocks to agree)

| Scenario | n | errors | mean | p50 | p90 | p95 | p99 | max | min |
|---|---|---|---|---|---|---|---|---|---|
| A sequential, 1 client | 1000 | 0 | 1.37 | 1.17 | 2.03 | 2.45 | 3.66 | 6.41 | 0.83 |
| B fan-out, 1 clients | 300 | 0 | 2.39 | 1.81 | 2.94 | 3.51 | 6.39 | 139.9 | 0.79 |
| B fan-out, 10 clients | 300 | 0 | 3.06 | 2.81 | 3.97 | 4.46 | 6.72 | 9.53 | 2.06 |
| B fan-out, 100 clients | 300 | 0 | 4.04 | 3.94 | 4.51 | 4.80 | 5.45 | 6.53 | 3.33 |
| C open-loop 10/s | 200 | 0 | 7.17 | 7.18 | 9.27 | 9.91 | 11.90 | 13.69 | 2.38 |
| C open-loop 50/s | 1000 | 0 | 4.33 | 3.76 | 6.60 | 7.17 | 9.13 | 22.50 | 1.69 |
| C open-loop 100/s | 2000 | 0 | 2.74 | 2.60 | 3.78 | 4.19 | 5.54 | 9.20 | 1.11 |
| C open-loop 200/s (late 214) | 4000 | 0 | 6.81 | 1.83 | 4.14 | 32.97 | 118.5 | 346.6 | 0.97 |
| D status path | 300 | 0 | 2.14 | 1.77 | 3.65 | 4.21 | 5.09 | 7.07 | 0.86 |

## Open-loop details

| rate | completions/s | generator lag p99 (ms) | bench CPU | api CPU max/mean | realtime CPU max | redis CPU max | postgres CPU max |
|---|---|---|---|---|---|---|---|
| 10/s | 10.0 | 2.81 | 5% | - / - | - | - | - |
| 50/s | 50.0 | 3.36 | 18% | - / - | - | - | - |
| 100/s | 100.0 | 4.98 | 26% | - / - | - | - | - |
| 200/s | 162.7 | 183.2 | 39% | - / - | - | - | - |


## Server-side histograms (Prometheus, same window; bucket-interpolated quantiles)

| Scenario | metric | count | mean | p50 | p95 | p99 |
|---|---|---|---|---|---|---|
| A | order_create_seconds | 1050 | 2.18 | 2.02 | 3.00 | 6.81 |
| A | order_event_publish_seconds | 1050 | 0.16 | 0.18 | 0.31 | 0.62 |
| A | realtime_fanout_seconds | 1050 | 0.03 | 0.05 | 0.10 | 0.10 |
| A | realtime_publish_to_send_seconds | 1050 | 0.27 | 0.21 | 0.49 | 0.98 |
| A | http_post_orders_seconds | 1050 | 4.21 | 3.88 | 6.50 | 14.58 |
| B1 | order_create_seconds | 320 | 5.04 | 2.65 | 13.85 | 47.50 |
| B1 | order_event_publish_seconds | 320 | 0.31 | 0.20 | 0.86 | 3.17 |
| B1 | realtime_fanout_seconds | 320 | 0.17 | 0.05 | 0.10 | 0.48 |
| B1 | realtime_publish_to_send_seconds | 320 | 0.77 | 0.32 | 1.67 | 4.83 |
| B1 | http_post_orders_seconds | 320 | 8.88 | 4.69 | 23.24 | 96.67 |
| B10 | order_create_seconds | 320 | 2.34 | 2.21 | 4.38 | 7.40 |
| B10 | order_event_publish_seconds | 320 | 0.16 | 0.18 | 0.35 | 0.49 |
| B10 | realtime_fanout_seconds | 320 | 0.03 | 0.05 | 0.10 | 0.10 |
| B10 | realtime_publish_to_send_seconds | 3200 | 0.30 | 0.24 | 0.50 | 0.97 |
| B10 | http_post_orders_seconds | 320 | 4.30 | 3.97 | 6.99 | 12.25 |
| B100 | order_create_seconds | 320 | 2.37 | 2.06 | 3.33 | 7.33 |
| B100 | order_event_publish_seconds | 320 | 0.17 | 0.18 | 0.33 | 0.80 |
| B100 | realtime_fanout_seconds | 320 | 0.07 | 0.05 | 0.12 | 0.23 |
| B100 | realtime_publish_to_send_seconds | 32000 | 0.48 | 0.40 | 0.97 | 2.38 |
| B100 | http_post_orders_seconds | 320 | 4.65 | 3.95 | 7.06 | 14.67 |
| C10 | order_create_seconds | 220 | 8.29 | 7.89 | 14.53 | 35.00 |
| C10 | order_event_publish_seconds | 220 | 0.45 | 0.39 | 0.99 | 2.30 |
| C10 | realtime_fanout_seconds | 220 | 0.08 | 0.06 | 0.22 | 0.25 |
| C10 | realtime_publish_to_send_seconds | 2200 | 0.78 | 0.74 | 2.07 | 2.49 |
| C10 | http_post_orders_seconds | 220 | 13.92 | 14.00 | 24.66 | 63.33 |
| C50 | order_create_seconds | 1020 | 4.10 | 3.82 | 5.30 | 27.86 |
| C50 | order_event_publish_seconds | 1020 | 0.27 | 0.20 | 0.48 | 1.52 |
| C50 | realtime_fanout_seconds | 1020 | 0.05 | 0.05 | 0.10 | 0.21 |
| C50 | realtime_publish_to_send_seconds | 10200 | 0.44 | 0.40 | 0.91 | 2.03 |
| C50 | http_post_orders_seconds | 1020 | 7.35 | 6.39 | 9.91 | 43.12 |
| C100 | order_create_seconds | 2020 | 4.81 | 2.48 | 4.71 | 123.7 |
| C100 | order_event_publish_seconds | 2020 | 0.28 | 0.18 | 0.44 | 4.38 |
| C100 | realtime_fanout_seconds | 2020 | 0.03 | 0.05 | 0.10 | 0.11 |
| C100 | realtime_publish_to_send_seconds | 20200 | 0.30 | 0.33 | 0.50 | 0.96 |
| C100 | http_post_orders_seconds | 2020 | 7.74 | 4.02 | 7.47 | 157.1 |
| C200 | order_create_seconds | 4020 | 18.25 | 1.93 | 133.2 | 429.3 |
| C200 | order_event_publish_seconds | 4020 | 0.52 | 0.19 | 3.30 | 7.64 |
| C200 | realtime_fanout_seconds | 4020 | 0.03 | 0.05 | 0.10 | 0.12 |
| C200 | realtime_publish_to_send_seconds | 40200 | 0.28 | 0.21 | 0.50 | 1.31 |
| C200 | http_post_orders_seconds | 4020 | 25.23 | 4.14 | 179.1 | 462.5 |
| D | order_create_seconds | 320 | 2.27 | 2.10 | 3.75 | 7.33 |
| D | order_event_publish_seconds | 640 | 0.23 | 0.21 | 0.48 | 0.87 |
| D | realtime_fanout_seconds | 640 | 0.03 | 0.05 | 0.10 | 0.18 |
| D | realtime_publish_to_send_seconds | 320 | 0.50 | 0.44 | 0.96 | 2.24 |
| D | http_post_orders_seconds | 320 | 4.38 | 3.93 | 7.00 | 14.50 |
| D | http_patch_status_seconds | 320 | 7.03 | 6.55 | 9.93 | 22.00 |

## Clock check (scenario A)

published_at minus send: p50 3.62 ms, min 2.39 ms; response minus published_at: p50 1.70 ms, min 1.15 ms; orders where either was negative: 0. A negative value would mean the Docker VM clock disagrees with the host clock.

## Latency budget (scenario A, means)

| Stage | mean ms |
|---|---|
| Client send to API `published_at`, excluding DB: network ingress (host to Docker port-forward), uvicorn parse, routing, post-commit reload, envelope build (= client mean of published_at minus t_send, minus the DB row below) | 1.84 |
| API pricing queries + INSERTs + COMMIT (`order_create_seconds`, Prometheus) | 2.18 |
| `published_at` to Go frame write (`realtime_publish_to_send_seconds`, Prometheus): Redis PUBLISH + hop + Go fan-out + queue + writer | 0.27 |
| Go WS write to client read (client pub_to_recv minus publish_to_send) | 1.10 |
| **Measured end-to-end (client, mean)** | 5.39 |

Reference points, not additive: 
- `order_event_publish_seconds` (API's Redis PUBLISH call): 0.16 ms
- `realtime_fanout_seconds` (Go lookup + enqueue): 0.03 ms
- Redis hop to Go subscriber (publish_to_send minus fanout, includes the PUBLISH): 0.25 ms
- API HTTP handler total (`http_request_duration_seconds`): 4.21 ms
- Client POST round trip: 5.89 ms; port-forward + client overhead = RTT minus handler = 1.68 ms (both directions)

Rows 1-4 sum to the measured end-to-end by construction (`published_at` is the pivot). Row 1 is a residual and is only meaningful if the host and Docker VM clocks agree; see the clock check. Server means are exact (Prometheus `_sum/_count` deltas), not bucket-interpolated. The HTTP handler total includes response serialisation, which happens after the publish and is not on the notification path.

