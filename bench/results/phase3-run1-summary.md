# Phase 3 latency benchmark: summary

Run: 2026-10-09 22:32:12 PDT on Apple M2 Pro, 16 GiB RAM; Docker VM: 10 CPUs, 8217382912 bytes, Docker Desktop, 29.2.0.
All times in milliseconds. e2e = kitchen frame received minus (intended) POST send time, same host clock. Percentiles are nearest-rank over every order (no outlier trimming). `errors` = HTTP failures + frames never received; `late` = frames that took more than 2 s (still included in the percentiles).

## End-to-end: diner tap to kitchen receives (last client of the fan-out)

| Scenario | n | errors | mean | p50 | p90 | p95 | p99 | max | min |
|---|---|---|---|---|---|---|---|---|---|
| A sequential, 1 client | 1000 | 0 | 7.31 | 5.36 | 8.71 | 15.29 | 34.44 | 487.3 | 3.49 |
| B fan-out, 1 clients | 300 | 0 | 8.95 | 6.11 | 13.80 | 18.85 | 59.58 | 86.03 | 3.95 |
| B fan-out, 10 clients | 300 | 0 | 7.97 | 6.00 | 11.25 | 18.46 | 37.87 | 122.6 | 4.02 |
| B fan-out, 100 clients | 300 | 0 | 8.14 | 7.07 | 9.08 | 12.88 | 27.35 | 56.42 | 5.63 |
| C open-loop 10/s | 200 | 0 | 30.42 | 16.79 | 48.60 | 71.46 | 336.7 | 478.6 | 9.00 |
| C open-loop 50/s | 1000 | 0 | 57.80 | 11.83 | 166.0 | 346.2 | 586.9 | 731.1 | 5.88 |
| C open-loop 100/s | 2000 | 0 | 21.44 | 7.62 | 10.93 | 79.69 | 377.8 | 526.6 | 4.84 |
| C open-loop 200/s (late 1522) | 4000 | 0 | 4112.8 | 705.1 | 14510.7 | 17568.5 | 22513.5 | 26323.6 | 4.84 |
| D status path | 300 | 0 | 9.22 | 8.37 | 11.63 | 13.48 | 35.41 | 50.90 | 4.99 |

## Fan-out: median client vs last client

| Scenario | n | errors | mean | p50 | p90 | p95 | p99 | max | min |
|---|---|---|---|---|---|---|---|---|---|
| B 1 clients, median client | 300 | 0 | 8.95 | 6.11 | 13.80 | 18.85 | 59.58 | 86.03 | 3.95 |
| B 1 clients, last client | 300 | 0 | 8.95 | 6.11 | 13.80 | 18.85 | 59.58 | 86.03 | 3.95 |
| B 10 clients, median client | 300 | 0 | 7.79 | 5.84 | 10.73 | 18.43 | 37.80 | 121.6 | 3.94 |
| B 10 clients, last client | 300 | 0 | 7.97 | 6.00 | 11.25 | 18.46 | 37.87 | 122.6 | 4.02 |
| B 100 clients, median client | 300 | 0 | 7.61 | 6.60 | 8.39 | 12.40 | 27.03 | 55.44 | 5.20 |
| B 100 clients, last client | 300 | 0 | 8.14 | 7.07 | 9.08 | 12.88 | 27.35 | 56.42 | 5.63 |

## POST round trip (what the diner's request takes)

| Scenario | n | errors | mean | p50 | p90 | p95 | p99 | max | min |
|---|---|---|---|---|---|---|---|---|---|
| A sequential, 1 client | 1000 | 0 | 7.77 | 5.73 | 9.49 | 15.48 | 36.57 | 497.9 | 3.86 |
| B fan-out, 1 clients | 300 | 0 | 9.54 | 6.63 | 14.80 | 19.96 | 59.47 | 85.96 | 4.40 |
| B fan-out, 10 clients | 300 | 0 | 7.94 | 6.03 | 10.89 | 18.61 | 37.64 | 123.0 | 3.93 |
| B fan-out, 100 clients | 300 | 0 | 6.50 | 5.50 | 7.42 | 11.23 | 24.05 | 53.05 | 4.07 |
| C open-loop 10/s | 200 | 0 | 30.55 | 16.84 | 49.95 | 73.64 | 340.3 | 487.9 | 8.72 |
| C open-loop 50/s | 1000 | 0 | 58.96 | 12.17 | 169.2 | 349.5 | 592.0 | 735.4 | 5.62 |
| C open-loop 100/s | 2000 | 0 | 21.82 | 7.72 | 11.05 | 84.11 | 384.3 | 532.1 | 5.12 |
| C open-loop 200/s (late 1522) | 4000 | 0 | 4184.8 | 763.1 | 14643.6 | 17642.2 | 22536.6 | 26349.4 | 4.80 |
| D status path | 300 | 0 | 9.76 | 9.12 | 12.22 | 13.60 | 35.28 | 50.86 | 5.30 |

## Publish to receive (envelope published_at_us to client frame; needs host/VM clocks to agree)

| Scenario | n | errors | mean | p50 | p90 | p95 | p99 | max | min |
|---|---|---|---|---|---|---|---|---|---|
| A sequential, 1 client | 1000 | 0 | 3.05 | 2.78 | 3.77 | 4.56 | 8.82 | 27.38 | 2.15 |
| B fan-out, 1 clients | 300 | 0 | 3.20 | 2.62 | 4.14 | 4.92 | 13.71 | 33.48 | 2.12 |
| B fan-out, 10 clients | 300 | 0 | 4.24 | 3.97 | 5.33 | 5.93 | 8.82 | 15.67 | 2.97 |
| B fan-out, 100 clients | 300 | 0 | 5.41 | 5.14 | 5.76 | 6.90 | 10.76 | 17.20 | 4.46 |
| C open-loop 10/s | 200 | 0 | 4.85 | 4.32 | 6.24 | 8.92 | 17.24 | 23.43 | 2.28 |
| C open-loop 50/s | 1000 | 0 | 4.86 | 4.17 | 6.50 | 8.15 | 15.80 | 218.8 | 1.26 |
| C open-loop 100/s | 2000 | 0 | 3.92 | 4.08 | 5.47 | 5.88 | 7.51 | 31.84 | 1.15 |
| C open-loop 200/s (late 1522) | 4000 | 0 | 37.68 | 15.50 | 108.0 | 162.3 | 281.7 | 609.5 | 0.98 |
| D status path | 300 | 0 | 3.00 | 2.73 | 4.04 | 4.37 | 6.77 | 9.63 | 2.01 |

## Open-loop details

| rate | completions/s | generator lag p99 (ms) | bench CPU | api CPU max/mean | realtime CPU max | redis CPU max | postgres CPU max |
|---|---|---|---|---|---|---|---|
| 10/s | 10.0 | 3.26 | 4% | - / - | - | - | - |
| 50/s | 50.0 | 92.05 | 17% | - / - | - | - | - |
| 100/s | 98.8 | 17.10 | 25% | - / - | - | - | - |
| 200/s | 108.3 | 381.5 | 59% | - / - | - | - | - |


## Server-side histograms (Prometheus, same window; bucket-interpolated quantiles)

| Scenario | metric | count | mean | p50 | p95 | p99 |
|---|---|---|---|---|---|---|
| A | order_create_seconds | 1050 | 3.08 | 2.15 | 6.78 | 22.00 |
| A | order_event_publish_seconds | 1050 | 0.24 | 0.19 | 0.48 | 2.12 |
| A | realtime_fanout_seconds | 1050 | 0.04 | 0.05 | 0.10 | 0.24 |
| A | realtime_publish_to_send_seconds | 2100 | 0.40 | 0.23 | 0.90 | 3.28 |
| A | http_post_orders_seconds | 1050 | 6.03 | 4.08 | 13.69 | 43.75 |
| B1 | order_create_seconds | 320 | 3.74 | 2.60 | 9.25 | 24.71 |
| B1 | order_event_publish_seconds | 320 | 0.29 | 0.20 | 0.81 | 2.14 |
| B1 | realtime_fanout_seconds | 320 | 0.07 | 0.05 | 0.15 | 0.98 |
| B1 | realtime_publish_to_send_seconds | 640 | 0.55 | 0.34 | 1.79 | 8.00 |
| B1 | http_post_orders_seconds | 320 | 7.21 | 4.65 | 20.00 | 49.38 |
| B10 | order_create_seconds | 320 | 2.99 | 2.27 | 8.00 | 22.00 |
| B10 | order_event_publish_seconds | 320 | 0.23 | 0.19 | 0.60 | 2.03 |
| B10 | realtime_fanout_seconds | 320 | 0.04 | 0.05 | 0.10 | 0.25 |
| B10 | realtime_publish_to_send_seconds | 3520 | 0.40 | 0.30 | 0.99 | 2.49 |
| B10 | http_post_orders_seconds | 320 | 5.90 | 4.17 | 15.00 | 40.83 |
| B100 | order_create_seconds | 320 | 2.79 | 2.30 | 8.25 | 14.14 |
| B100 | order_event_publish_seconds | 320 | 0.22 | 0.18 | 0.44 | 3.40 |
| B100 | realtime_fanout_seconds | 320 | 0.08 | 0.05 | 0.15 | 0.25 |
| B100 | realtime_publish_to_send_seconds | 32320 | 0.55 | 0.43 | 1.26 | 3.86 |
| B100 | http_post_orders_seconds | 320 | 5.44 | 4.11 | 15.00 | 30.00 |
| C10 | order_create_seconds | 220 | 11.80 | 6.47 | 38.46 | 96.67 |
| C10 | order_event_publish_seconds | 220 | 0.63 | 0.28 | 2.25 | 9.00 |
| C10 | realtime_fanout_seconds | 220 | 0.16 | 0.06 | 0.40 | 2.35 |
| C10 | realtime_publish_to_send_seconds | 2420 | 1.00 | 0.49 | 3.72 | 11.04 |
| C10 | http_post_orders_seconds | 220 | 21.16 | 10.86 | 66.67 | 316.7 |
| C50 | order_create_seconds | 1020 | 28.83 | 4.21 | 193.8 | 451.6 |
| C50 | order_event_publish_seconds | 1020 | 1.19 | 0.23 | 4.70 | 14.20 |
| C50 | realtime_fanout_seconds | 1020 | 0.09 | 0.05 | 0.17 | 0.34 |
| C50 | realtime_publish_to_send_seconds | 11190 | 0.75 | 0.42 | 1.70 | 3.71 |
| C50 | http_post_orders_seconds | 1020 | 38.64 | 7.01 | 225.8 | 490.6 |
| C100 | order_create_seconds | 2020 | 10.29 | 2.51 | 35.23 | 270.5 |
| C100 | order_event_publish_seconds | 2020 | 0.42 | 0.18 | 1.31 | 7.87 |
| C100 | realtime_fanout_seconds | 2020 | 0.04 | 0.05 | 0.10 | 0.10 |
| C100 | realtime_publish_to_send_seconds | 20200 | 0.32 | 0.34 | 0.58 | 1.07 |
| C100 | http_post_orders_seconds | 2020 | 15.10 | 4.16 | 57.41 | 382.6 |
| C200 | order_create_seconds | 4020 | 27.41 | 3.13 | 183.7 | 443.1 |
| C200 | order_event_publish_seconds | 4020 | 1.00 | 0.24 | 4.47 | 9.18 |
| C200 | realtime_fanout_seconds | 4020 | 0.04 | 0.05 | 0.10 | 0.19 |
| C200 | realtime_publish_to_send_seconds | 40200 | 0.46 | 0.30 | 0.72 | 2.04 |
| C200 | http_post_orders_seconds | 4020 | 49.25 | 16.17 | 229.2 | 468.9 |
| D | order_create_seconds | 320 | 2.07 | 1.90 | 2.97 | 4.96 |
| D | order_event_publish_seconds | 640 | 0.23 | 0.20 | 0.48 | 0.99 |
| D | realtime_fanout_seconds | 640 | 0.03 | 0.05 | 0.10 | 0.15 |
| D | realtime_publish_to_send_seconds | 320 | 0.48 | 0.41 | 0.96 | 2.28 |
| D | http_post_orders_seconds | 320 | 3.87 | 3.82 | 5.00 | 8.63 |
| D | http_patch_status_seconds | 320 | 6.69 | 6.23 | 9.83 | 30.00 |

## Clock check (scenario A)

published_at minus send: p50 2.54 ms, min 1.18 ms; response minus published_at: p50 3.21 ms, min 2.52 ms; orders where either was negative: 0. A negative value would mean the Docker VM clock disagrees with the host clock.

## Latency budget (scenario A, means)

| Stage | mean ms |
|---|---|
| Client send to API `published_at`, excluding DB: network ingress (host to Docker port-forward), uvicorn parse, routing, post-commit reload, envelope build (= client mean of published_at minus t_send, minus the DB row below) | 1.18 |
| API pricing queries + INSERTs + COMMIT (`order_create_seconds`, Prometheus) | 3.08 |
| `published_at` to Go frame write (`realtime_publish_to_send_seconds`, Prometheus): Redis PUBLISH + hop + Go fan-out + queue + writer | 0.40 |
| Go WS write to client read (client pub_to_recv minus publish_to_send) | 2.66 |
| **Measured end-to-end (client, mean)** | 7.31 |

Reference points, not additive: 
- `order_event_publish_seconds` (API's Redis PUBLISH call): 0.24 ms
- `realtime_fanout_seconds` (Go lookup + enqueue): 0.04 ms
- Redis hop to Go subscriber (publish_to_send minus fanout, includes the PUBLISH): 0.36 ms
- API HTTP handler total (`http_request_duration_seconds`): 6.03 ms
- Client POST round trip: 7.77 ms; port-forward + client overhead = RTT minus handler = 1.74 ms (both directions)

Rows 1-4 sum to the measured end-to-end by construction (`published_at` is the pivot). Row 1 is a residual and is only meaningful if the host and Docker VM clocks agree; see the clock check. Server means are exact (Prometheus `_sum/_count` deltas), not bucket-interpolated. The HTTP handler total includes response serialisation, which happens after the publish and is not on the notification path.

