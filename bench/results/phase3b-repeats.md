# Phase 3b: before/after (1 worker, pre_ping) vs (2 workers, explicit pool)

e2e ms, diner send to kitchen frame (last client). Medians are over runs. Before = `phase3-repeats.md`; after = `phase3b-*` JSON. `<100` = runs with p99 under 100 ms.

| Path | Scenario | Version | runs | p99<100 | median p50 | median p95 | median p99 | worst p99 | p99 per run |
|---|---|---|---|---|---|---|---|---|---|
| host | A sequential | before | 3 | 3/3 | 5.36 | 9.89 | 17.75 | 34.44 | 34.44, 17.75, 15.18 |
| host | A sequential | after | 1 | 1/1 | 4.01 | 6.57 | 9.94 | 9.94 | 9.94 |
| host | C 50/s | before | 8 | 2/8 | 11.19 | 18.12 | 163.2 | 777.3 | 586.9, 40.97, 46.44, 102.3, 777.3, 209.7, 116.8, 211.8 |
| host | C 50/s | after | 3 | 2/3 | 10.41 | 15.33 | 31.30 | 113.7 | 21.33, 113.7, 31.30 |
| host | C 100/s | before | 9 | 2/9 | 7.81 | 79.69 | 377.8 | 20117.1 | 377.8, 3050.2, 232.5, 163.8, 10612.4, 545.4, 26.27, 20117.1, 14.90 |
| host | C 100/s | after | 3 | 2/3 | 7.34 | 11.13 | 26.55 | 384.5 | 26.55, 384.5, 12.51 |
| host | C 200/s | before | 4 | 0/4 | 24.90 | 12271.5 | 15184.0 | 22513.5 | 22513.5, 10131.7, 5286.8, 20236.2 |
| host | C 200/s | after | 3 | 2/3 | 5.52 | 8.45 | 17.49 | 834.3 | 17.49, 10.34, 834.3 |
| in-network | A sequential | before | 1 | 1/1 | 3.17 | 4.40 | 5.72 | 5.72 | 5.72 |
| in-network | A sequential | after | 1 | 1/1 | 2.61 | 3.78 | 4.98 | 4.98 | 4.98 |
| in-network | C 50/s | before | 5 | 4/5 | 10.07 | 13.35 | 21.99 | 122.3 | 122.3, 19.04, 21.99, 15.75, 23.75 |
| in-network | C 50/s | after | 1 | 1/1 | 9.61 | 12.97 | 29.01 | 29.01 | 29.01 |
| in-network | C 100/s | before | 5 | 4/5 | 6.63 | 8.53 | 17.55 | 217.3 | 17.55, 93.66, 12.88, 217.3, 9.54 |
| in-network | C 100/s | after | 3 | 3/3 | 6.55 | 9.53 | 31.07 | 55.50 | 23.49, 55.50, 31.07 |
| in-network | C 200/s | before | 5 | 1/5 | 5.59 | 54.04 | 337.4 | 6965.0 | 337.4, 76.00, 401.2, 270.1, 6965.0 |
| in-network | C 200/s | after | 4 | 3/4 | 4.58 | 10.67 | 60.71 | 235.0 | 72.74, 48.68, 235.0, 23.46 |
