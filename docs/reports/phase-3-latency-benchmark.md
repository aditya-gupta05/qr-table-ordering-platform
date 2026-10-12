# Phase 3: End-to-end latency benchmark and Grafana dashboard

## Summary
Phase 3 measures the resume claim "kitchen order notification under 100 ms" instead of asserting it. [bench/e2e_latency.py](../../bench/e2e_latency.py)
drives four scenarios (sequential, fan-out, open-loop load, status path), timestamps everything on one host clock, scrapes
Prometheus for the matching server-side histograms, builds a latency budget, and always deletes its rows. The suite was run 3 times on
the host path plus about 15 extra runs (5 repeats of the open-loop rates, 6 in-network runs, idle-gap and CPU diagnostics) because open-loop tails were noisy.
A [Grafana dashboard](../../ops/grafana/dashboards/realtime-latency.json) shows the same server-side numbers live.

Headline, honestly: **p99 under 100 ms holds for one-order-at-a-time and for 100-client fan-out in every run (worst p99 59.6 ms). It does not
hold reliably under sustained open-loop load on this laptop through Docker Desktop's port forward.** Individual outliers crossed 100 ms
(A max 487 ms in run 1). Details below; raw data in [bench/results/](../../bench/results/), summary in
[phase3-summary.md](../../bench/results/phase3-summary.md), variance in [phase3-repeats.md](../../bench/results/phase3-repeats.md).

Files: [e2e_latency.py](../../bench/e2e_latency.py), [phase3_repeats.py](../../bench/phase3_repeats.py),
[healthz_open_loop.py](../../bench/healthz_open_loop.py), [realtime-latency.json](../../ops/grafana/dashboards/realtime-latency.json),
bucket changes in [metrics.go](../../realtime/internal/hub/metrics.go), [metrics.py](../../api/app/metrics.py), [middleware.py](../../api/app/middleware.py).

## 1. Technologies, languages, libraries, frameworks

**Python asyncio + httpx + `websockets` (new api dev dependency).** One event loop holds the HTTP client and up to 100 WebSocket readers, so every
timestamp comes from the same process and the same `time.time_ns()` clock. `websockets` 17 has an asyncio client with `compression=None`
(avoids deflate cost on the client) and `ping_interval=None`. Alternative: k6 or wrk2. k6 has proper open-loop executors, but cannot
cheaply correlate "this POST" with "that WebSocket frame" or read the envelope's `published_at_us`; wrk2 is HTTP only. Cost: a Python
generator is itself a noisy instrument at high rate (its own CPU share is reported per rate, 26% of a core at 100/s, 39 to 60% at 200/s).

**`time.time_ns()` for every stamp, nanoseconds.** The envelope's `published_at_us` is epoch microseconds, so the only way to relate client and
server stamps is wall clock. First attempt scheduled sends on `loop.time()` (monotonic); that was a bug (section 2).

**Prometheus HTTP API (`/api/v1/query`) for server-side histograms, deltas computed in the bench.** The bench snapshots cumulative bucket
counters before and after each scenario (waiting scrape interval + 1.5 s so the last scrape lands) and re-implements PromQL's
`histogram_quantile` interpolation on the delta. Alternative: `histogram_quantile(.., increase(x[Ns]))` per window; `increase` extrapolates at
window edges and the window would have to be aligned to scrapes, so deltas of instant queries are exact for counts and sums.

**`asyncpg` directly for cleanup and token lookup.** Already an api dependency; deletes `order_items` then `orders` where `notes='bench-phase3'`
in a `finally`. Alternative: `docker compose exec psql`; would not work from inside a container run.

**Grafana provisioned JSON, built from a generator script.** 12 panels, datasource uid `prometheus`. Threshold line at 0.1 s uses
`thresholdsStyle: line`. Alternative: Grafonnet/Jsonnet; overkill for one dashboard.

**Histogram buckets (changed).** See section 2, "Bucket choice".

## 2. Technical challenges and how they were approached

### 2.1 Coordinated omission, open-loop vs closed-loop
A closed-loop client (send, wait for the answer, send the next) slows down exactly when the server slows down, so it never sends the
requests that would have queued, and the stall is recorded once instead of for every request that would have waited behind it. Scenario A and B
are closed-loop **on purpose** (they measure the uncontended path). Scenario C is open-loop: order `i` is *due* at `T0 + i/rate`, a task is spawned
at that time regardless of outstanding responses, and the latency clock starts at the *due* time (`t_send_ns` is intended time; `t_actual_ns`
is recorded separately and its lag is reported as `gen_lag_ms`). If the generator itself falls behind (it did at 200/s: generator lag p99 183 ms to 652 ms),
the lateness counts against the system under test, which is the conservative direction. Frames never received are errors; frames over 2 s are
flagged `late` but their latency stays in the percentiles (C at 200/s: 214 late in the final run, 627 and 1522 in others). Nothing is trimmed.
Evidence the distinction matters: at 200/s from the host, p50 is 6.65 ms but p95 is 2383 ms; a closed-loop client would have reported far lower numbers because the 2.4 s queue never forms when the client waits for each answer (not measured separately).

### 2.2 Percentiles vs averages; why p99 for a kitchen screen
At 200/s (host path, final run) the mean is 287.6 ms and the p50 is 6.65 ms: a mean describes neither the typical order nor the unlucky one.
A kitchen board is looked at continuously and an order arriving 400 ms late is noticed; with ~1 order/s at peak, a p99 of 100 ms is one
slow ticket every ~2 minutes, which is the tolerable threshold. p50 says what the system does, p99 says how often it embarrasses you, and the max
(reported, not hidden) says what one unlucky ticket looks like. Nearest-rank percentiles over raw per-order samples (not interpolated
histograms) are used for the client numbers; with n=1000, p99 is the 10th largest sample, so one bad second moves it. That is why p99 on 200
orders (scenario C at 10/s) is only the 2nd largest sample and is flagged as weak in the repeats table.

### 2.3 Clock domains: host vs Docker Desktop VM
`published_at_us` is taken in the API container (VM clock). `t_recv` is taken on the macOS host. They are different clocks, so
`pub_to_recv = t_recv - published_at` is only trustworthy if the VM is synchronised. The headline `e2e = t_recv - t_send` uses only the host clock and is immune.
Check: in sequential scenario A, `published_at` must lie between `t_send` and `t_resp`. Result per run
([summary files](../../bench/results/)): run 1 min(published minus send) 1.18 ms, 0 negatives; run 3 min 2.39 ms, 0 negatives; **run 2: 765 of 1000 orders negative, p50 of
published minus send = -0.89 ms**, i.e. the VM clock was about 4 ms behind the host during that run. So `pub_to_recv` and the "client send to published_at" row of the budget
are only quoted from runs with zero negatives (run 1, run 3, and the in-network run where client and containers share the VM clock: `pub_to_recv` mean 0.38 ms). A direct
`docker exec ... date` comparison was useless (exec latency about 190 ms, 100x the skew being measured).

A second clock bug was mine: the first version scheduled the open-loop timetable on `loop.time()` (monotonic) but stamped on `time_ns()` (wall). macOS slews the
wall clock, and over a 20 s run intended and actual times drifted by about 0.8 ms/s, producing **negative** `gen_lag` and a p50 e2e of -4.6 ms by the end of the 100/s run.
A negative latency is a sanity check that cannot be explained physically, which is how it was caught. Fix: schedule on the same wall clock the samples use
(`delay = (t0_ns + i*1e9/rate - now_ns())/1e9`); the later runs have no negative `gen_lag`.

### 2.4 Client-side and server-side numbers, and why both
Client e2e answers "what does the diner/kitchen experience", including the Docker port forward, kernel, TLS (none here) and my own Python loop. Server histograms
(`order_create_seconds`, `order_event_publish_seconds`, `realtime_fanout_seconds`, `realtime_publish_to_send_seconds`) answer "where inside our code". Neither alone is enough:
the Go histogram says publish-to-socket-write p99 is about 1 ms at 100/s while the client sees a p99 of 232 ms in the same run; that gap is the proof that the tail
is not in the Go hub (section 2.8). Conversely, the server cannot see queueing before the handler starts or the host path, so it would have reported a healthy system
for a run the client saw as broken. Quantile caveat: server quantiles are interpolated within buckets; means (`_sum/_count`) are exact.

### 2.5 Bucket choice and quantile accuracy
`histogram_quantile` assumes observations are uniformly distributed inside the bucket that contains the rank, so the worst-case error is the bucket's width,
and anything in `+Inf` is reported as the highest finite bound (a *lower* bound on the truth, which under-reports overload). What was wrong and what changed:

| Metric | Before | After | Why |
|---|---|---|---|
| `realtime_publish_to_send_seconds`, `realtime_fanout_seconds` | 0.1 ms ... 250 ms | added 75 ms, 500 ms, 1 s | 50 to 100 ms is a 50 ms-wide bucket right at the 100 ms line; 75 ms halves it. Above 250 ms an overloaded p99 would saturate at 0.25 and look like "250 ms" |
| `order_create_seconds` | 2 ms ... 1 s (9 buckets) | 1, 2, 3, 5, 7.5, 10, 15, 25 ms ... 1 s | observed p50 2.0 ms and mean 2.18 ms sit at the first bucket edge; almost no resolution |
| `order_event_publish_seconds` | 0.5 ms ... 500 ms | added 0.1, 0.25 ms and 1 s | measured mean 0.16 ms, below the old smallest bucket |
| `http_request_duration_seconds` | 5 ms ... 5 s | added 2.5, 7.5, 15 ms | POST /api/orders mean is 4.2 ms |

Still too coarse, stated plainly: `realtime_fanout_seconds` has its first bucket at 0.1 ms and the real value is 0.03 ms mean, so its reported p50 (0.05) and p95
(0.10) are interpolation artefacts, not measurements; only its mean (exact) and "well under 0.25 ms" are meaningful. Not worth another rebuild for a stage that is 0.03 of 5.4 ms.
Cross-check of accuracy: scenario A server publish-to-send p99 = 0.98 ms (bucket-interpolated, 0.5 to 1 ms bucket), while the client's pub_to_recv p99 = 4.77 ms includes the WS write,
the host path and the Python reader, so they are consistent rather than equal.

### 2.6 The latency budget (scenario A, final run, means, ms)
Pivot is `published_at_us`, which is measured on both sides:

| Stage | Host path | In-network | Source |
|---|---|---|---|
| Client send to `published_at`, minus DB: port forward, uvicorn parse, routing, post-commit reload, envelope build | 1.84 | 1.31 | client (published minus send) minus Prometheus `order_create_seconds` |
| Pricing queries + INSERTs + COMMIT | 2.18 | 1.64 | `order_create_seconds` |
| `published_at` to Go frame write (Redis PUBLISH + hop + fan-out + writer) | 0.27 | 0.20 | `realtime_publish_to_send_seconds` |
| Go WS write to client read | 1.10 | 0.18 | client `pub_to_recv` minus the row above |
| **End-to-end mean** | **5.39** | **3.32** | client |

Reference, not additive: API Redis PUBLISH call 0.16 ms; Go fan-out (lookup and enqueue) 0.03 ms; handler total 4.21 ms (includes response serialisation after the publish, so it is not on the
notification path); client POST RTT 5.89 ms, of which 1.68 ms is host path plus client. So **Redis and the Go hub together are about 0.3 ms of a 5.4 ms notification (5%)**; the DB commit is 40%
and the HTTP/ingress side 34%. Running the load generator inside the compose network removes about 2.1 ms (39%) of the mean, almost all in the ingress and the last WS hop: that is the Docker Desktop
port-forward tax a Linux host would not have. Caveat: the first row is a residual and is only valid where clocks agree (run 3 and the in-network run satisfy this).

### 2.7 What the 100 ms claim is actually backed by
- Sequential (A), 3 runs: p50 4.9 to 5.4 ms, p95 7.3 to 15.3 ms, p99 15.2 to 34.4 ms; max 41 to 487 ms.
- Fan-out to 100 kitchen clients (B), 3 runs, last client to receive: p50 6.6 to 7.5 ms, p99 10.7 to 27.4 ms. Fan-out cost is small: 100 clients add about 1 to 2 ms over 1 client at p50 (5.77 to 6.60 final run, 5.05 to 7.49 in run 2).
  The frame count confirms every client got every frame: `realtime_publish_to_send_seconds` count for B100 was 32000 for 320 orders (320 x 100) in the final run, 32320 in run 1 (one extra idle kitchen tab, see 2.10).
- Status path (D): p50 9.09, p99 28.8 ms (3 runs: 17.6 to 35.4).
- **Idle-gap effect, not an artefact of open loop:** A with a 100 ms pause before each order has p50 21.7 ms (vs 5.4); with a 1 s pause p50 27.1, p99 57.9. Same code, same sequential client, only
  idleness changed. Open-loop at 10/s shows the same thing (p50 16.8, 19.2, 23.6 ms over three runs, p99 337, 179, 60). Explanation (hypothesis, consistent with the evidence but not proven): idle Docker VM vCPUs and
  macOS efficiency-core scheduling make a request after a gap pay a wake-up cost; sustained load keeps the cores hot, which is why 100/s has a *lower* p50 (7.6 ms) than 10/s. A real kitchen gets an order every
  minute or so, i.e. closer to the 1 s-gap numbers (p50 27, p99 58 ms) than to scenario A. That is still under 100 ms, but it is the number that should be quoted for "typical", not 5 ms.
- Server side only, API publish to Go socket write: p99 under 5 ms at every rate up to 200/s, because it is a handful of in-memory operations plus one Redis hop.

### 2.8 Why the open-loop tail is not the realtime path; what limits throughput
Evidence collected, in order:

1. **Host path, C at 100/s and 200/s.** Completions per second at target 200: 76, 108, 142, 163, 114, 137 across runs (never 200), and two 100/s runs collapsed (p50 1374 and 4343 ms, one HTTP error each).
2. **CPU is not saturated.** cgroup `cpu.stat` deltas around a host-path 200/s run ([phase3-cgroup-cpu.txt](../../bench/results/phase3-cgroup-cpu.txt)): api 14.0 CPU-s over 31.4 s wall (0.45 core average), postgres 4.9 CPU-s (0.15 core),
   redis 0.39 CPU-s. `docker stats` sampled during runs (`--diag`) agreed: api max 85 to 110%, mean 37 to 51%; realtime max 8% to 34%.
3. **Postgres is mostly idle waiting for the API.** `pg_stat_activity` sampled every 250 ms during 200/s: mean 4.3 idle + 1.4 idle-in-transaction (wait: Client), 0.1 active. Only about 5.7 backends open, well below the SQLAlchemy pool ceiling (5 + 10 overflow), so it is
   not pool exhaustion. Connections waiting on the client are waiting on the API's event loop or the network.
4. **In-network run changes the answer** (load generator in a container on the compose network, same code): 200/s completes 200.0/s with 0 errors in 6 of 6 runs; 100/s never collapsed. api CPU for a 200/s run: 10.46 CPU-s for 4020 orders = **2.6 ms per order**, about 0.5 core at 200/s;
   Postgres 0.67 ms/order; so a single uvicorn worker's CPU ceiling is roughly 380 orders/s, and this run did not hit it.
5. A GET /healthz open loop (no DB, no Redis; [phase3-healthz-baseline.txt](../../bench/results/phase3-healthz-baseline.txt)) at 50/s from the host: p50 6.1 to 6.3 ms, **p99 35 to 81 ms, max 100 to 201 ms**. The endpoint that does nothing has a tail
   as bad as the order path at 50/s; at 200/s it was p99 10 to 15 ms (hot cores again).

Conclusions I would defend in an interview: (a) the Go hub and Redis are not the limiter at any tested rate; (b) the **host-path collapse at 100 to 200/s is a Docker Desktop port-forward effect** (disappears in-network, with CPU, DB and pool all idle),
the likely mechanism being per-connection setup and a feedback loop (late responses mean more concurrent connections) but I did not prove that mechanism and a Linux host run would be the control; (c) **inside the compose network the remaining p99 tail at 200/s
(76 to 401 ms in 4 of 6 runs, and 6965 ms once) comes from `order_create_seconds` (p99 118 to 430 ms on the server side)**: the DB phase of the single API process (pool checkout, 6 to 7 statements, COMMIT with `fsync=on` and `synchronous_commit=on` on Docker's virtual disk).
That the stall is in the DB phase is measured; which part of it (WAL fsync vs event-loop scheduling vs GC) I did not isolate. Not a dead end, a stated gap.

What I would change, in order (none implemented; asks at the end of the task): (1) run uvicorn with 2 to 4 workers (the API has no in-process state; the Redis client and engine are per process); note Prometheus then needs multiprocess mode;
(2) set `pool_size` and `max_overflow` explicitly and drop `pool_pre_ping` (one extra round trip per checkout) in favour of `pool_recycle`; (3) collapse the 6 to 7 statements to fewer round trips (`INSERT ... RETURNING` for items plus one reload join) -
`load_order` after commit is about 1.8 ms of the handler; (4) experiment with `synchronous_commit=off` (or a group-commit delay) *for the diagnostic only* to isolate the WAL fsync; (5) re-run on a Linux host or CI VM to remove the port-forward.

### 2.9 What 10x load looks like
Normal peak for one restaurant is on the order of 1 order/s; the benchmark's 10x (10/s) is easily met on the realtime side. For 10x the *tested* ceiling (about 200/s inside the network): the API would saturate first (one worker, ~380/s CPU ceiling, tails from 200/s), then Postgres commit rate; the Go hub
would still be at about 8% CPU (fan-out to 10 clients at 200/s is 2000 frames/s) and publish-to-send p99 about 1 ms. Redis pub/sub being at-most-once, an API or hub overload shows up as slow orders, not lost notifications, until the hub's per-client queue (64) overflows,
which would be visible as `realtime_slow_consumer_drops_total` (0 in every run) and trigger the REST resync.

### 2.10 Other problems hit (problem, cause, fix)
| Problem | Cause | Fix |
|---|---|---|
| First full run looked terrible at 10/s (p99 166 ms) and 100/s (negative e2e) | Timetable on monotonic clock vs wall stamps (2.3); also a `docker stats` poller running during the measurement | Wall-clock timetable; `docker stats` and `pg_stat_activity` sampling moved behind `--diag`, off for headline runs. That run was discarded and is only described here (its numbers: C10 p99 166, C100 p95 222, C200 completions 76/s) |
| Latency budget summed to 8.3 ms vs 7.1 ms measured | "Handler total minus create minus publish" includes response serialisation after the publish | Rebuilt the budget around the `published_at` pivot (2.6) |
| Frames per order was 11 instead of 10 in some runs (e.g. B100 count 32320, run 1) | A Vite-proxied kitchen browser tab (the user's) was connected during the earlier runs, one extra idle client | Left in place (not mine to close); costs one extra frame per order in the server histograms, not in client samples. It was gone by the final run (32000) |
| Run-to-run spread bigger than the effect (C50 p99 587 vs 41 vs 46) | Host is a busy laptop: load average 6 to 8 on 10 cores, 15 GiB used with a 7.4 GiB compressor | Not tuned away. Repeated: 8 host runs of C50 and 9 of C100, 5 in-network, results reported with pass counts |
| Grafana stat panels "No data" for counters with no series yet | `order_events_publish_failures_total` is never exported until first increment | `sum(...) or vector(0)` on the three stat panels |
| `make lint` does not cover `bench/` | ruff runs in `api/` only | Bench files carry `# ruff: noqa: E501` and pass `ruff check` and `ruff format --check` when invoked explicitly |
| `--label -run2` rejected by argparse | A value starting with `-` is parsed as a flag | Use `--label=-run2` |

No escalation to `architect` was needed. Not verified: browser paint time (the end point is the socket read, not DOM update); TLS; more than one hub instance; Linux host numbers; the stall root cause in 2.8.

## 3. Deployment, automation, monitoring, logging

- **Grafana dashboard "Realtime latency"** (uid `qr-realtime-latency`, [realtime-latency.json](../../ops/grafana/dashboards/realtime-latency.json), provisioned from the existing dashboards mount; `overview.json` kept). Panels:
  publish-to-send p50/p95/p99 with a red 100 ms threshold line; `order_create` and `order_event_publish` p95; fan-out p95/p50; connections by `topic_kind`; events received/s by channel; frames sent/s; HTTP request rate and p95 by route;
  stat panels for slow-consumer drops, resyncs, publish failures, open connections. Each query was executed against Prometheus `query_range` over the benchmark window and returned series (frames sent peaked at 1053/s, HTTP rate at 105/s; the publish-to-send p99 line peaked at 68 ms in one 15 s step of the 1 m rate window, a low-sample window that the per-scenario bench deltas, p99 under 5 ms, do not reproduce, which is worth knowing when reading the panel). `curl localhost:3000/api/search` lists both dashboards; the page rendered in the browser pane with all panels titled and legends populated.
  Caveat on the 100 ms line: `realtime_publish_to_send_seconds` starts at `published_at` and ends at socket write, so it is the server's share of the budget, not the diner-to-kitchen number (which only the bench measures).
- **Histogram buckets** on three services (table in 2.5); `api` and `realtime` images rebuilt (`docker compose up -d --build api realtime`).
- **Dependency:** `websockets` as an api dev dependency (approved), `api/pyproject.toml` and `uv.lock`.
- **Scripts:** `bench/e2e_latency.py` (suite, per-scenario output, cleanup `finally`), `bench/phase3_repeats.py` (variance table), `bench/healthz_open_loop.py` (GET floor). Commands added to `AGENTS.md`.
- **In-network run (no port forward)**, reuses the api image and installs the two client libraries in the throwaway container:
  ```bash
  docker run --rm --network qr-ordering_default --user root -v $PWD/bench:/bench --entrypoint sh qr-ordering-api -c \
    "uv pip install -q --python /opt/venv/bin/python httpx websockets && python /bench/e2e_latency.py --scenario A --scenario C \
     --rates 50 100 200 --base http://api:8000 --ws ws://realtime:8080 --prom http://prometheus:9090 \
     --db-url postgresql://qr:qr@postgres:5432/qr --label=-docker"
  ```
- **Logging:** none changed. Cleanup evidence: `select count(*) from orders where notes='bench-phase3'` returns 0 after every run (printed by the bench), and no orphan `order_items` remain.

## Likely interview questions

1. **How did you measure under 100 ms?**
   From the client: a Python process stamps `t_send` when the POST is due, holds a WebSocket on `/ws/kitchen`, and stamps `t_recv` when the matching `order.created` frame (matched by `public_id`, which can arrive before the POST response) is read.
   Same `time_ns()` clock for both, so no cross-host skew. Server-side Prometheus histograms are scraped for the same window and used for the budget. Closed-loop for the clean path, open-loop with intended-time stamping for load, 3 suite runs plus repeats because the machine is noisy.
   Result: p99 15 to 34 ms sequential, 11 to 27 ms with 100 clients, but open-loop at 50/s and up through the Mac port forward does not reliably stay under 100 ms p99.

2. **What is coordinated omission?**
   When a load generator waits for each response before sending the next request, a stall also stops the generator from sending the requests that would have waited behind the stall, so the percentiles under-report. Fix: schedule on a fixed timetable and measure from the
   scheduled time (`t_send_ns = t0 + i/rate`, `bench/e2e_latency.py`). Here at 200/s the open-loop p95 is 2383 ms while p50 is 6.65 ms; a closed loop would have shown far less (not measured separately).

3. **Why p99 and not the average?**
   Averages hide the bimodal case: at 200/s the mean is 288 ms and the median is 6.7 ms; neither describes a user. For a kitchen screen the cost is one visibly late ticket, so the tail rate matters. We report p50, p90, p95, p99, max and mean for every metric, and say that p99 on 200 samples is only the 2nd largest sample.

4. **Where does the time go?**
   Mean 5.4 ms end to end on the host path: 2.2 ms DB (pricing, inserts, commit), 1.8 ms HTTP ingress, routing and reload, 0.3 ms Redis plus Go hub (fan-out itself is 0.03 ms), 1.1 ms WebSocket write and the client read.
   Inside the compose network it is 3.3 ms. Redis and Go are about 5% of the budget; the database and the HTTP layer are about 75%.

5. **What happens at 10x load?**
   Define 10x: normal peak is about 1 order/s, so 10/s is trivial. 10x the tested 200/s would hit the API first: one uvicorn worker (about 2.6 ms CPU per order, about 380/s ceiling) and the commit path; tails already appear at 200/s in-network (p99 76 to 401 ms).
   The Go hub is at about 8% CPU at 200/s with 10 clients. Mitigations: more uvicorn workers, explicit pool sizing, fewer round trips; the hub scales by running N instances because each subscribes to Redis.

6. **Why measure both client-side and server-side?**
   The server histogram cannot see the host path, the queue before the handler, or the client. The client number cannot say which stage is slow. Example: same 100/s run, client p99 232 ms, Go publish-to-send p99 about 1 ms: the tail is not in the hub.

7. **Why did you change the histogram buckets, and how do buckets affect accuracy?**
   `histogram_quantile` interpolates linearly inside the bucket containing the rank, so error is up to the bucket width, and anything past the last bucket is reported as that bound (an under-estimate). `order_create_seconds` had its median in the first bucket;
   the Go histograms had a 50 ms-wide bucket at the 100 ms line and stopped at 250 ms. I added buckets at 1 to 3 ms, 75 ms, and 0.5 and 1 s. Known remaining gap: `realtime_fanout_seconds` cannot resolve below 0.1 ms; only its mean is trustworthy.

8. **Your clocks differ between the container and the host. How did you handle that?**
   The headline metric uses only the host clock. The cross-clock metric (`pub_to_recv`) is validated by a sanity check: `published_at` must lie between `t_send` and `t_resp`. In run 2 that failed for 765 of 1000 orders (VM about 4 ms behind), so I quote cross-clock numbers only from runs where it passed and from the in-network run (single clock).
   I also found my own wall-vs-monotonic scheduling bug because latency came out negative.

9. **Why is p50 at 10 orders/s (about 20 ms) worse than at 100 orders/s (7.6 ms)?**
   It is an idle-wake effect, reproduced without open loop: sequential with a 100 ms gap gives p50 21.7 ms; with a 1 s gap 27.1 ms, vs 4.9 ms back to back. Hot cores respond faster. It matters because a real kitchen is nearer the idle case, so "typical" is about 27 ms p50 and 58 ms p99 on this machine.

10. **What did the benchmark not prove, and what would you do next?**
    No Linux host numbers; no browser paint time; the in-network 200/s tail is located in the order-create DB phase but not root-caused (WAL fsync vs event loop vs GC); the host-path collapse mechanism is inferred. Next: Linux run, `synchronous_commit=off` experiment, multiple uvicorn workers, and `pg_stat_wal` timing.

## Numbers

All times ms. Final run = canonical files `bench/results/phase3-*.json` and `phase3-summary.md`; command: `cd api && uv run python ../bench/e2e_latency.py --suite` (about 6 min). Variance: `python3 bench/phase3_repeats.py -run1 -run2 "" -rep1 ... -docker-cgroup`.

| Scenario (final run) | n | errors | p50 | p90 | p95 | p99 | max | mean |
|---|---|---|---|---|---|---|---|---|
| A sequential, 1 client | 1000 | 0 | 4.94 | 6.41 | 7.28 | 15.18 | 51.66 | 5.39 |
| B fan-out 1 client | 300 | 0 | 5.77 | 15.33 | 23.08 | 55.06 | 257.3 | 9.67 |
| B fan-out 10, last client | 300 | 0 | 5.82 | 8.04 | 8.99 | 12.41 | 18.83 | 6.18 |
| B fan-out 100, last client | 300 | 0 | 6.60 | 8.00 | 8.82 | 10.71 | 11.68 | 6.85 |
| B fan-out 100, median client | 300 | 0 | 6.09 | 7.38 | 8.04 | 10.31 | 11.33 | 6.33 |
| C 10/s | 200 | 0 | 23.62 | 30.11 | 33.18 | 60.39 | 84.69 | 23.15 |
| C 50/s | 1000 | 0 | 11.25 | 14.70 | 17.04 | 46.44 | 105.0 | 12.56 |
| C 100/s | 2000 | 0 | 7.65 | 10.49 | 13.07 | 232.5 | 387.6 | 12.63 |
| C 200/s (214 late over 2 s; 162.7 completions/s) | 4000 | 0 | 6.65 | 364.0 | 2383 | 5287 | 5886 | 287.6 |
| D status change to diner socket | 300 | 0 | 9.09 | 12.51 | 14.02 | 28.80 | 98.12 | 10.11 |
| A, in-network (single run) | 1000 | 0 | 3.17 | - | 4.40 | 5.72 | 14.48 | 3.32 |
| A, 100 ms idle gap | 300 | 0 | 21.72 | - | 30.12 | 38.11 | 53.91 | 21.1 |
| A, 1 s idle gap | 100 | 0 | 27.11 | - | 44.92 | 57.91 | 73.12 | 27.7 |

Run-to-run (p99 of e2e): A 34.4 / 17.8 / 15.2; B100 27.4 / 13.1 / 10.7; D 35.4 / 17.6 / 28.8; C10 337 / 179 / 60; C50 host (8 runs) 587, 41, 46, 102, 777, 210, 117, 212; C100 host (9 runs, 2 collapsed) 378, 3050, 233, 15, 164, 10612, 545, 26, 20117;
C100 in-network (5 runs) 17.6, 93.7, 12.9, 217, 9.5; C200 in-network (6 runs) 337, 76, 401, 270, 6965, 195. Passes under 100 ms p99: A/B/D 3 of 3; C50 host 2 of 8; C100 host 2 of 9; C100 in-network 4 of 5; C200 in-network 1 of 6.

Server side, final run (Prometheus deltas; p50 / p95 / p99 bucket-interpolated, mean exact):

| Scenario | `order_create` mean / p99 | `publish_to_send` mean / p99 | `fanout` mean | `order_event_publish` mean / p99 |
|---|---|---|---|---|
| A | 2.18 / 6.81 | 0.27 / 0.98 | 0.03 | 0.16 / 0.62 |
| B 100 clients | 2.37 / 7.33 | 0.48 / 2.38 | 0.07 | 0.17 / 0.80 |
| C 50/s | 4.10 / 27.9 | 0.44 / 2.03 | 0.05 | 0.27 / 1.52 |
| C 100/s | 4.81 / 123.8 | 0.30 / 0.96 | 0.03 | 0.28 / 4.38 |
| C 200/s | 18.25 / 429.3 | 0.28 / 1.31 | 0.03 | 0.52 / 7.64 |

Resource evidence: host-path C200 cgroup CPU api 14.0 s / 31.4 s wall, postgres 4.9 s, redis 0.39 s ([phase3-cgroup-cpu.txt](../../bench/results/phase3-cgroup-cpu.txt)); in-network C200 api 10.46 CPU-s for 4020 orders (2.6 ms/order), postgres 2.71 s, redis 0.37 s;
healthz open loop at 50/s p99 35 to 81 ms ([phase3-healthz-baseline.txt](../../bench/results/phase3-healthz-baseline.txt)). Tests: `make test` 33 API tests plus Go `-race` pass; `make lint` clean (2 existing oxlint warnings in web/). Machine: Apple M2 Pro 10 cores, 16 GiB,
Docker Desktop 29.2.0 VM 10 CPUs 7.65 GiB, host load average 6 to 8 during runs.

## Follow-up: workers and pool sizing

### Problem
Section 2.8 located the open-loop tail in the API process: one uvicorn worker (one event loop, one core of Python, 2.6 ms CPU per order, so a ceiling of about 380 orders/s) plus a SQLAlchemy pool with default sizing and `pool_pre_ping=True`.
In-network C200 passed p99 < 100 ms in 1 of 5 comparable runs (p99 76 to 6965 ms), and the host path collapsed at 100 to 200/s in several runs. Two changes were approved: more workers, and explicit pool sizing.

### Change
| Area | Before | After | Where |
|---|---|---|---|
| Processes | `uvicorn app.main:app` (1 worker) | `--workers ${API_WORKERS:-2}` | [api/entrypoint.sh](../../api/entrypoint.sh), [Dockerfile](../../api/Dockerfile), [docker-compose.yml](../../docker-compose.yml) |
| Prometheus | single-process default registry | multiprocess mode: `PROMETHEUS_MULTIPROC_DIR=/tmp/prom_multiproc` (tmpfs in compose), wiped by the entrypoint before uvicorn starts; `/metrics` builds a fresh `CollectorRegistry` + `MultiProcessCollector` per scrape | [api/app/routers/health.py](../../api/app/routers/health.py) |
| DB pool | defaults (5 + 10 overflow), `pool_pre_ping=True` | `pool_size=10`, `max_overflow=10`, `pool_recycle=1800`, no pre_ping (env `DB_POOL_SIZE`, `DB_MAX_OVERFLOW`, `DB_POOL_RECYCLE`) | [api/app/db.py](../../api/app/db.py), [api/app/config.py](../../api/app/config.py) |

Single-process fallback: with `PROMETHEUS_MULTIPROC_DIR` unset (pytest, `uv run uvicorn` on the host) `/metrics` uses the normal registry. Tests: 35 pass (two new: multiprocess branch returns 200, engine reflects pool settings and has pre_ping off).

Things checked for single-process assumptions: the Redis client is a lazily created module global ([events.py](../../api/app/events.py)), so each worker makes its own after startup; the engine is created at import in each worker; structlog is configured at import per worker; startup/shutdown hooks only log and dispose that worker's own engine/Redis. No in-process caches or shared state exist. Alembic runs once in the entrypoint before uvicorn forks, so workers do not race on migrations.

**Worker count: 2.** Measured only 2 (not 4). Reasoning: the Docker VM has 10 CPUs shared with Postgres, Go, Redis, Prometheus and the load generator; at 2.6 ms CPU per order two workers give about 760/s of CPU headroom, nearly 4x the 200/s tested, and api CPU at 200/s is about 0.36 core in total. 4 workers would add DB connections (below) and context switching without a measured need. It is an env var; I would re-test 4 on a box where the bench is not co-located.

**Pool math.** Postgres `max_connections` = 100 (checked with `show max_connections`; 3 are superuser-reserved). Worst case = workers x (pool_size + max_overflow) = 2 x (10 + 10) = **40**, leaving 57 for alembic, psql, the bench's own `asyncpg` connections and a second API replica of the same shape. Observed use was far lower: 21 backends on database `qr` right after the C200 runs (idle pooled connections from both workers plus the Prometheus-visible bench and psql sessions); section 2.8 had measured about 5.7 busy backends with one worker. 4 workers at 10 + 10 would be 80, too close to 100, so raising `API_WORKERS` means lowering `DB_POOL_SIZE`/`DB_MAX_OVERFLOW` in the same change. `pool_recycle=1800` closes connections older than 30 minutes so a NAT/firewall idle timeout or a server-side kill does not leave a pool full of dead sockets forever.

### Prometheus with several processes
Each worker keeps its counters and histogram buckets in its own mmap files (`counter_<pid>.db`, `histogram_<pid>.db`) in the multiproc dir. A scrape can land on any worker, so that worker's `/metrics` reads **all** files and sums them (`MultiProcessCollector`), on a fresh registry each time (a shared registry would raise duplicate-registration on the second scrape). Verification: 200 `GET /readyz` requests round-robined by the kernel across both workers (`counter_13.db` and `counter_14.db` both existed), then `http_requests_total{route="/readyz"} == 200` and `http_request_duration_seconds_count == 200` from one `/metrics` call (before: no series). The bench's Prometheus-delta tables work unchanged because they use `sum by (le)`.
Known limits: (1) **no gauges exist in the API today**, so there was nothing to give a `multiprocess_mode`; any future gauge must set one explicitly (`livesum`, `max`, ...) or the library defaults to `all` and emits a series per pid. (2) **`mark_process_dead` is not called**: uvicorn 0.54 has no child-exit hook (gunicorn has `child_exit`). Consequence: if a worker dies and is respawned, its files stay and its counters keep contributing, which is correct for counters and histograms (cumulative totals must not drop); it would only matter for `live*` gauges. (3) The default `process_*`/`python_gc_*` collectors are not part of a multiprocess registry, so those series disappeared from `/metrics`; nothing in the dashboards used them. (4) Dir is tmpfs, wiped on every container start so stale files cannot double count.

### Dropping `pool_pre_ping`: restart behaviour (measured)
Trade-off: pre_ping issues a `SELECT 1`-style round trip on every checkout (one in-network round trip; I did not measure it in isolation, so it is not part of the numbers above) to hide dead connections. Without it a connection killed while idle in the pool is only discovered when a request uses it; that request fails once, SQLAlchemy invalidates the pool for that worker (all connections older than the failure), and later requests open fresh connections. Mitigations: `pool_recycle`, and the client's idempotency key makes retrying `POST /orders` safe (no duplicate order).
Test: warm both workers' pools with 80 concurrent order POSTs, `docker compose restart postgres`, wait for `pg_isready`, then send a sequence (script outside the repo; 10 iterations of POST, POST, readyz after the restart; second pass starting with readyz):
- Starting with POST: the first POST returned **500** (`connection is closed`), then 201s; a second 500 appeared once more later on the other worker (each worker has its own pool, so each pays one failure); `/readyz` returned 200 every time. Everything else (28 of 30 requests) succeeded.
- Starting with `/readyz`: the first `/readyz` returned **503** (`postgres: fail`, the probe's own connection was stale), the first POST after it returned **500**, no further failure in that run.
- Net: **about one failed request per worker per Postgres restart**, then self-healing with no API restart. Logged as `Exception in ASGI application ... InterfaceError: connection is closed` (and, in the warm step, one 500 left over from the first restart in the same experiment). Cost accepted for the saved round trip; if a probe needs zero failures, `/readyz` could keep `pool_pre_ping` on a dedicated engine.
After the test, the `restart-test` orders were deleted and no orphan `order_items` remain.

### Before / after
Same commands as section 3 (host: `cd api && uv run python ../bench/e2e_latency.py --scenario A --scenario C --rates 50 100 200 --label b`, plus `--label b-rep1/b-rep2`; in-network: the `docker run` above with `--label=-b-docker` renamed to `phase3b-docker*`, `-rep1`, `-rep2`).
Before = [phase3-repeats.md](../../bench/results/phase3-repeats.md) (1 worker, pre_ping). After = [phase3b-repeats.md](../../bench/results/phase3b-repeats.md), generated by [bench/phase3b_compare.py](../../bench/phase3b_compare.py), raw in `bench/results/phase3b-*`. e2e ms, last client; medians over runs.

| Path | Scenario | Version | runs | p99 < 100 ms | median p50 | median p95 | median p99 | worst p99 |
|---|---|---|---|---|---|---|---|---|
| host | A | before | 3 | 3/3 | 5.36 | 9.89 | 17.75 | 34.4 |
| host | A | after | 1 | 1/1 | 4.01 | 6.57 | 9.94 | 9.94 |
| host | C 50/s | before | 8 | 2/8 | 11.19 | 18.12 | 163 | 777 |
| host | C 50/s | after | 3 | 2/3 | 10.41 | 15.33 | 31.3 | 114 |
| host | C 100/s | before | 9 | 2/9 | 7.81 | 79.69 | 378 | 20117 |
| host | C 100/s | after | 3 | 2/3 | 7.34 | 11.13 | 26.6 | 385 |
| host | C 200/s | before | 4 | 0/4 | 24.90 | 12272 | 15184 | 22514 |
| host | C 200/s | after | 3 | 2/3 | 5.52 | 8.45 | 17.5 | 834 |
| in-network | A | before | 1 | 1/1 | 3.17 | 4.40 | 5.72 | 5.72 |
| in-network | A | after | 1 | 1/1 | 2.61 | 3.78 | 4.98 | 4.98 |
| in-network | C 50/s | before | 5 | 4/5 | 10.07 | 13.35 | 22.0 | 122 |
| in-network | C 50/s | after | 1 | 1/1 | 9.61 | 12.97 | 29.0 | 29.0 |
| in-network | C 100/s | before | 5 | 4/5 | 6.63 | 8.53 | 17.6 | 217 |
| in-network | C 100/s | after | 3 | 3/3 | 6.55 | 9.53 | 31.1 | 55.5 |
| in-network | C 200/s | before | 5 | 1/5 | 5.59 | 54.04 | 337 | 6965 |
| in-network | C 200/s | after | 4 (3 + 1 CPU-measurement run) | 3/4 | 4.58 | 10.67 | 60.7 | 235 |

Server side, in-network `order_create_seconds` (mean / p99 ms): C200 before 7.30 / 215 and 3.04 / 53.8 (two docker runs); after 2.16 / 15.3, 2.35 / 22.5, 2.83 / 42.3, 1.66 / 10.2 (four runs). C100 before 2.43 / 7.0 and 3.82 / 46; after 2.42 / 10.4, 2.65 / 21.8, 2.44 / 13.5.
Zero HTTP errors in every after run; the open-loop schedule was kept (no late-send collapse) in all of them, host C200 included.

**api CPU during in-network C200** (cgroup `cpu.stat` delta, both workers, [phase3b-docker-cgroup-before.txt](../../bench/results/phase3b-docker-cgroup-before.txt) / `-after.txt`): api 10.38 CPU-s over 29.1 s wall for 4020 orders = **2.58 ms per order** (before: 10.46 CPU-s, 2.6 ms) and about 0.36 core average; postgres 2.16 CPU-s (0.54 ms per order, before 0.67). Per-order CPU did not change, which is expected: workers add parallelism, not efficiency. What changed is the queueing: two event loops mean a slow DB commit or a GC pause stalls half of the arrivals rather than all of them.

How to read this honestly: improvements are large on the host path at 200/s (0 of 4 to 2 of 3 passing, the collapse of 5 to 22 s is gone) and in-network C200 (1 of 5 to 3 of 4, median p99 337 to 61 ms), small elsewhere. Sample sizes after are 3 to 4, before 4 to 9, and p99 here is dominated by single stalls: one host C100 run still hit 385 ms and one host C200 run 834 ms, one in-network C200 run 235 ms. I did not isolate whether the remaining stalls are WAL fsync, the Docker VM, or the laptop's load average. The change was applied together with the pool change, so I cannot attribute the gain between the two; the pool change (one fewer round trip per checkout) is expected to be a smaller effect than the second event loop, but that is reasoning, not measurement. "C200 passes p99 < 100 ms" is still not a claim for every run.

### Trade-offs
- More memory (two Python processes, about 2x resident) and more DB connections; the pool math must be redone when `API_WORKERS` changes.
- Metrics are now per-pid files: scrape cost grows with the number of files, no process-level default collectors, and gauges need a `multiprocess_mode`.
- No `mark_process_dead` (see above); no pre_ping (one failed request per worker after a DB restart).
- The WebSocket-side latency is unchanged: the realtime service already is a separate Go process; this only affects `POST /orders` and reads.

### Interview Q&A
1. **Why multiple workers instead of more async?** The handler is already async; what saturates is a single event loop on a single core running Python between awaits (JSON parsing, pricing, building the envelope: 2.6 ms of CPU per order). `async` lets one process overlap I/O waits, it does not add cores. A DB stall also blocks everything scheduled on that loop. Two processes give two loops and two independent pools. More async tasks per loop would just make the same loop busier.
2. **How does Prometheus work with multiple processes?** Each process holds its own metric values, but a scrape hits just one. In multiprocess mode every process writes its values to mmap files in `PROMETHEUS_MULTIPROC_DIR`, and `/metrics` aggregates all files on a fresh `CollectorRegistry` with `MultiProcessCollector` (counters and histogram buckets are summed; gauges depend on `multiprocess_mode`). The dir must be emptied at startup, otherwise old pids' counts would be double counted. I verified it: 200 requests spread over two workers show as 200 in one scrape.
3. **How did you size the DB pool?** Start from the hard limit, Postgres `max_connections=100`, divide by processes: workers x (pool_size + max_overflow) = 2 x 20 = 40 worst case, with the rest left for migrations, tooling and a second replica. Then check the real demand: Postgres was mostly idle, about 5.7 backends busy at 200/s with one worker, so 10 + 10 per worker is generous headroom against bursts, not a throughput requirement. `pool_recycle=1800` handles idle-timeout style staleness.
4. **What's the GIL's role here?** One Python process executes Python bytecode on one thread at a time, so a single uvicorn worker can use about one core no matter how many cores exist. asyncio avoids waiting on I/O but not the GIL, and threads would not help CPU-bound sections. Separate worker processes each have their own interpreter and GIL, so two workers can use two cores. The cost is that processes do not share memory, which is why metrics needed multiprocess mode and the Redis client and DB pool are per process.
5. **What did dropping `pool_pre_ping` cost?** One failed request per worker after a Postgres restart (measured above), in exchange for removing a round trip per checkout; idempotency keys make client retries safe.
