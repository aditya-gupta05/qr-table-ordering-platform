# Phase 3 latency benchmark: summary

Run: 2026-10-09 22:35:50 PDT on Apple M2 Pro, 16 GiB RAM; Docker VM: 10 CPUs, 8217382912 bytes, Docker Desktop, 29.2.0.
All times in milliseconds. e2e = kitchen frame received minus (intended) POST send time, same host clock. Percentiles are nearest-rank over every order (no outlier trimming). `errors` = HTTP failures + frames never received; `late` = frames that took more than 2 s (still included in the percentiles).

## End-to-end: diner tap to kitchen receives (last client of the fan-out)

| Scenario | n | errors | mean | p50 | p90 | p95 | p99 | max | min |
|---|---|---|---|---|---|---|---|---|---|
| A sequential, 1 client | 1000 | 0 | 6.07 | 5.36 | 8.25 | 9.89 | 17.75 | 41.23 | 3.26 |
| B fan-out, 1 clients | 300 | 0 | 5.67 | 5.05 | 7.41 | 9.50 | 15.79 | 26.07 | 3.44 |
| B fan-out, 10 clients | 300 | 0 | 5.86 | 5.53 | 6.93 | 8.10 | 14.38 | 16.19 | 4.00 |
| B fan-out, 100 clients | 300 | 0 | 7.82 | 7.49 | 9.12 | 10.09 | 13.11 | 15.40 | 6.01 |
| C open-loop 10/s | 200 | 0 | 25.99 | 19.16 | 30.20 | 58.67 | 179.2 | 333.7 | 9.03 |
| C open-loop 50/s | 1000 | 0 | 12.64 | 11.52 | 15.72 | 18.26 | 40.97 | 104.0 | 6.67 |
| C open-loop 100/s (late 71) | 2000 | 0 | 325.1 | 9.15 | 991.1 | 1695.7 | 3050.2 | 4610.5 | 5.15 |
| C open-loop 200/s (late 627) | 4000 | 0 | 1007.8 | 7.12 | 4485.4 | 8021.1 | 10131.7 | 11128.3 | 3.76 |
| D status path | 300 | 0 | 9.42 | 8.96 | 12.15 | 13.95 | 17.57 | 58.34 | 5.21 |

## Fan-out: median client vs last client

| Scenario | n | errors | mean | p50 | p90 | p95 | p99 | max | min |
|---|---|---|---|---|---|---|---|---|---|
| B 1 clients, median client | 300 | 0 | 5.67 | 5.05 | 7.41 | 9.50 | 15.79 | 26.07 | 3.44 |
| B 1 clients, last client | 300 | 0 | 5.67 | 5.05 | 7.41 | 9.50 | 15.79 | 26.07 | 3.44 |
| B 10 clients, median client | 300 | 0 | 5.71 | 5.41 | 6.75 | 8.04 | 13.79 | 16.16 | 3.92 |
| B 10 clients, last client | 300 | 0 | 5.86 | 5.53 | 6.93 | 8.10 | 14.38 | 16.19 | 4.00 |
| B 100 clients, median client | 300 | 0 | 7.29 | 7.04 | 8.49 | 9.33 | 12.43 | 14.86 | 5.48 |
| B 100 clients, last client | 300 | 0 | 7.82 | 7.49 | 9.12 | 10.09 | 13.11 | 15.40 | 6.01 |

## POST round trip (what the diner's request takes)

| Scenario | n | errors | mean | p50 | p90 | p95 | p99 | max | min |
|---|---|---|---|---|---|---|---|---|---|
| A sequential, 1 client | 1000 | 0 | 6.63 | 5.88 | 9.07 | 10.64 | 17.70 | 43.17 | 3.64 |
| B fan-out, 1 clients | 300 | 0 | 6.26 | 5.60 | 8.17 | 10.52 | 16.41 | 26.82 | 3.85 |
| B fan-out, 10 clients | 300 | 0 | 5.81 | 5.46 | 7.01 | 8.29 | 13.46 | 16.33 | 3.94 |
| B fan-out, 100 clients | 300 | 0 | 6.29 | 5.91 | 7.65 | 8.62 | 11.92 | 14.05 | 4.38 |
| C open-loop 10/s | 200 | 0 | 26.00 | 19.05 | 29.96 | 55.43 | 178.9 | 333.7 | 9.38 |
| C open-loop 50/s | 1000 | 0 | 12.71 | 11.55 | 15.83 | 18.63 | 40.88 | 104.5 | 6.93 |
| C open-loop 100/s (late 71) | 2000 | 0 | 336.2 | 9.29 | 1034.7 | 1715.2 | 3075.3 | 4671.4 | 4.95 |
| C open-loop 200/s (late 627) | 4000 | 0 | 1023.4 | 7.52 | 4513.0 | 8078.6 | 10180.6 | 11140.5 | 4.09 |
| D status path | 300 | 0 | 10.11 | 9.64 | 12.81 | 13.90 | 17.96 | 58.64 | 5.83 |

## Publish to receive (envelope published_at_us to client frame; needs host/VM clocks to agree)

| Scenario | n | errors | mean | p50 | p90 | p95 | p99 | max | min |
|---|---|---|---|---|---|---|---|---|---|
| A sequential, 1 client | 1000 | 0 | 6.40 | 6.13 | 7.28 | 7.82 | 9.76 | 14.65 | 5.66 |
| B fan-out, 1 clients | 300 | 0 | 3.07 | 1.79 | 6.21 | 6.77 | 7.70 | 8.73 | 1.16 |
| B fan-out, 10 clients | 300 | 0 | 2.37 | 2.20 | 3.21 | 3.51 | 4.52 | 8.18 | 1.35 |
| B fan-out, 100 clients | 300 | 0 | 3.74 | 3.57 | 4.29 | 4.67 | 6.03 | 9.43 | 2.95 |
| C open-loop 10/s | 200 | 0 | 5.79 | 5.54 | 7.61 | 8.85 | 13.16 | 16.60 | 2.40 |
| C open-loop 50/s | 1000 | 0 | 4.12 | 3.83 | 5.71 | 6.63 | 10.34 | 22.11 | 1.56 |
| C open-loop 100/s (late 71) | 2000 | 0 | 15.52 | 3.13 | 37.24 | 58.69 | 207.0 | 281.1 | 1.14 |
| C open-loop 200/s (late 627) | 4000 | 0 | 12.99 | 2.25 | 42.79 | 77.64 | 142.1 | 1070.4 | 1.03 |
| D status path | 300 | 0 | 2.27 | 2.10 | 3.47 | 3.88 | 4.98 | 9.23 | 1.14 |

## Open-loop details

| rate | completions/s | generator lag p99 (ms) | bench CPU | api CPU max/mean | realtime CPU max | redis CPU max | postgres CPU max |
|---|---|---|---|---|---|---|---|
| 10/s | 10.0 | 4.34 | 4% | - / - | - | - | - |
| 50/s | 50.0 | 4.93 | 18% | - / - | - | - | - |
| 100/s | 96.0 | 192.3 | 38% | - / - | - | - | - |
| 200/s | 142.4 | 652.4 | 47% | - / - | - | - | - |


## Server-side histograms (Prometheus, same window; bucket-interpolated quantiles)

| Scenario | metric | count | mean | p50 | p95 | p99 |
|---|---|---|---|---|---|---|
| A | order_create_seconds | 1050 | 2.59 | 2.28 | 4.90 | 9.88 |
| A | order_event_publish_seconds | 1050 | 0.23 | 0.18 | 0.43 | 1.42 |
| A | realtime_fanout_seconds | 1050 | 0.03 | 0.05 | 0.10 | 0.23 |
| A | realtime_publish_to_send_seconds | 1050 | 0.40 | 0.25 | 0.76 | 2.17 |
| A | http_post_orders_seconds | 1050 | 5.04 | 4.14 | 9.21 | 22.31 |
| B1 | order_create_seconds | 320 | 2.43 | 1.95 | 4.67 | 12.00 |
| B1 | order_event_publish_seconds | 320 | 0.18 | 0.18 | 0.43 | 0.89 |
| B1 | realtime_fanout_seconds | 320 | 0.02 | 0.05 | 0.10 | 0.10 |
| B1 | realtime_publish_to_send_seconds | 320 | 0.29 | 0.21 | 0.50 | 1.68 |
| B1 | http_post_orders_seconds | 320 | 4.58 | 4.00 | 8.61 | 17.67 |
| B10 | order_create_seconds | 320 | 2.29 | 1.80 | 4.14 | 7.43 |
| B10 | order_event_publish_seconds | 320 | 0.15 | 0.18 | 0.30 | 0.70 |
| B10 | realtime_fanout_seconds | 320 | 0.03 | 0.05 | 0.10 | 0.10 |
| B10 | realtime_publish_to_send_seconds | 3200 | 0.27 | 0.21 | 0.49 | 0.96 |
| B10 | http_post_orders_seconds | 320 | 4.31 | 3.89 | 7.36 | 13.80 |
| B100 | order_create_seconds | 320 | 2.60 | 2.50 | 4.33 | 6.00 |
| B100 | order_event_publish_seconds | 320 | 0.17 | 0.18 | 0.25 | 0.48 |
| B100 | realtime_fanout_seconds | 320 | 0.08 | 0.06 | 0.20 | 0.25 |
| B100 | realtime_publish_to_send_seconds | 32000 | 0.48 | 0.41 | 0.96 | 2.26 |
| B100 | http_post_orders_seconds | 320 | 5.19 | 4.43 | 7.35 | 11.33 |
| C10 | order_create_seconds | 220 | 10.56 | 7.40 | 21.67 | 167.5 |
| C10 | order_event_publish_seconds | 220 | 0.45 | 0.37 | 1.17 | 3.50 |
| C10 | realtime_fanout_seconds | 220 | 0.10 | 0.06 | 0.29 | 0.97 |
| C10 | realtime_publish_to_send_seconds | 2200 | 0.81 | 0.68 | 2.36 | 4.67 |
| C10 | http_post_orders_seconds | 220 | 16.51 | 12.03 | 39.29 | 190.0 |
| C50 | order_create_seconds | 1020 | 4.04 | 3.84 | 6.80 | 16.14 |
| C50 | order_event_publish_seconds | 1020 | 0.28 | 0.21 | 0.53 | 1.72 |
| C50 | realtime_fanout_seconds | 1020 | 0.05 | 0.05 | 0.10 | 0.22 |
| C50 | realtime_publish_to_send_seconds | 10200 | 0.46 | 0.40 | 0.92 | 2.12 |
| C50 | http_post_orders_seconds | 1020 | 7.28 | 6.48 | 11.67 | 32.00 |
| C100 | order_create_seconds | 2020 | 32.22 | 2.71 | 242.8 | 496.1 |
| C100 | order_event_publish_seconds | 2020 | 0.97 | 0.20 | 4.62 | 15.50 |
| C100 | realtime_fanout_seconds | 2020 | 0.04 | 0.05 | 0.10 | 0.21 |
| C100 | realtime_publish_to_send_seconds | 20200 | 0.36 | 0.35 | 0.83 | 2.15 |
| C100 | http_post_orders_seconds | 2020 | 44.55 | 4.98 | 304.7 | 611.5 |
| C200 | order_create_seconds | 4020 | 30.01 | 2.23 | 204.0 | 638.0 |
| C200 | order_event_publish_seconds | 4020 | 0.84 | 0.21 | 4.42 | 9.34 |
| C200 | realtime_fanout_seconds | 4020 | 0.03 | 0.05 | 0.10 | 0.17 |
| C200 | realtime_publish_to_send_seconds | 40200 | 0.44 | 0.24 | 0.59 | 1.26 |
| C200 | http_post_orders_seconds | 4020 | 40.52 | 4.58 | 233.9 | 685.7 |
| D | order_create_seconds | 320 | 1.98 | 1.66 | 3.29 | 6.00 |
| D | order_event_publish_seconds | 640 | 0.21 | 0.20 | 0.47 | 0.83 |
| D | realtime_fanout_seconds | 640 | 0.03 | 0.05 | 0.10 | 0.17 |
| D | realtime_publish_to_send_seconds | 320 | 0.45 | 0.41 | 0.93 | 2.05 |
| D | http_post_orders_seconds | 320 | 3.66 | 3.83 | 5.67 | 9.00 |
| D | http_patch_status_seconds | 320 | 6.38 | 6.16 | 9.59 | 14.25 |

## Clock check (scenario A)

published_at minus send: p50 -0.89 ms, min -2.44 ms; response minus published_at: p50 6.73 ms, min 5.97 ms; orders where either was negative: 765. A negative value would mean the Docker VM clock disagrees with the host clock.

## Latency budget (scenario A, means)

| Stage | mean ms |
|---|---|
| Client send to API `published_at`, excluding DB: network ingress (host to Docker port-forward), uvicorn parse, routing, post-commit reload, envelope build (= client mean of published_at minus t_send, minus the DB row below) | -2.92 |
| API pricing queries + INSERTs + COMMIT (`order_create_seconds`, Prometheus) | 2.59 |
| `published_at` to Go frame write (`realtime_publish_to_send_seconds`, Prometheus): Redis PUBLISH + hop + Go fan-out + queue + writer | 0.40 |
| Go WS write to client read (client pub_to_recv minus publish_to_send) | 6.00 |
| **Measured end-to-end (client, mean)** | 6.07 |

Reference points, not additive: 
- `order_event_publish_seconds` (API's Redis PUBLISH call): 0.23 ms
- `realtime_fanout_seconds` (Go lookup + enqueue): 0.03 ms
- Redis hop to Go subscriber (publish_to_send minus fanout, includes the PUBLISH): 0.37 ms
- API HTTP handler total (`http_request_duration_seconds`): 5.04 ms
- Client POST round trip: 6.63 ms; port-forward + client overhead = RTT minus handler = 1.59 ms (both directions)

Rows 1-4 sum to the measured end-to-end by construction (`published_at` is the pivot). Row 1 is a residual and is only meaningful if the host and Docker VM clocks agree; see the clock check. Server means are exact (Prometheus `_sum/_count` deltas), not bucket-interpolated. The HTTP handler total includes response serialisation, which happens after the publish and is not on the notification path.

