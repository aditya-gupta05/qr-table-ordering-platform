# Phase 3: run-to-run variance

Same suite, same machine, repeated. e2e in ms (diner send to kitchen frame, last client).

| Scenario | run | n | errors | p50 | p95 | p99 | max |
|---|---|---|---|---|---|---|---|
| host: A sequential | run1 | 1000 | 0 | 5.36 | 15.29 | 34.44 | 487.3 |
| host: B 1 clients (last) | run1 | 300 | 0 | 6.11 | 18.85 | 59.58 | 86.03 |
| host: B 10 clients (last) | run1 | 300 | 0 | 6.00 | 18.46 | 37.87 | 122.6 |
| host: B 100 clients (last) | run1 | 300 | 0 | 7.07 | 12.88 | 27.35 | 56.42 |
| host: C 10/s | run1 | 200 | 0 | 16.79 | 71.46 | 336.7 | 478.6 |
| host: C 50/s | run1 | 1000 | 0 | 11.83 | 346.2 | 586.9 | 731.1 |
| host: C 100/s | run1 | 2000 | 0 | 7.62 | 79.69 | 377.8 | 526.6 |
| host: C 200/s | run1 | 4000 | 0 | 705.1 | 17568.5 | 22513.5 | 26323.6 |
| host: D status path | run1 | 300 | 0 | 8.37 | 13.48 | 35.41 | 50.90 |
| host: A sequential | run2 | 1000 | 0 | 5.36 | 9.89 | 17.75 | 41.23 |
| host: B 1 clients (last) | run2 | 300 | 0 | 5.05 | 9.50 | 15.79 | 26.07 |
| host: B 10 clients (last) | run2 | 300 | 0 | 5.53 | 8.10 | 14.38 | 16.19 |
| host: B 100 clients (last) | run2 | 300 | 0 | 7.49 | 10.09 | 13.11 | 15.40 |
| host: C 10/s | run2 | 200 | 0 | 19.16 | 58.67 | 179.2 | 333.7 |
| host: C 50/s | run2 | 1000 | 0 | 11.52 | 18.26 | 40.97 | 104.0 |
| host: C 100/s | run2 | 2000 | 0 | 9.15 | 1695.7 | 3050.2 | 4610.5 |
| host: C 200/s | run2 | 4000 | 0 | 7.12 | 8021.1 | 10131.7 | 11128.3 |
| host: D status path | run2 | 300 | 0 | 8.96 | 13.95 | 17.57 | 58.34 |
| host: A sequential | final | 1000 | 0 | 4.94 | 7.28 | 15.18 | 51.66 |
| host: B 1 clients (last) | final | 300 | 0 | 5.77 | 23.08 | 55.06 | 257.3 |
| host: B 10 clients (last) | final | 300 | 0 | 5.82 | 8.99 | 12.41 | 18.83 |
| host: B 100 clients (last) | final | 300 | 0 | 6.60 | 8.82 | 10.71 | 11.68 |
| host: C 10/s | final | 200 | 0 | 23.62 | 33.18 | 60.39 | 84.69 |
| host: C 50/s | final | 1000 | 0 | 11.25 | 17.04 | 46.44 | 105.0 |
| host: C 100/s | final | 2000 | 0 | 7.65 | 13.07 | 232.5 | 387.6 |
| host: C 200/s | final | 4000 | 0 | 6.65 | 2383.0 | 5286.8 | 5885.7 |
| host: D status path | final | 300 | 0 | 9.09 | 14.02 | 28.80 | 98.12 |
| host: C 50/s | rep1 | 1000 | 0 | 10.91 | 16.99 | 102.3 | 190.6 |
| host: C 100/s | rep1 | 2000 | 0 | 7.89 | 14.04 | 163.8 | 314.8 |
| host: C 50/s | rep2 | 1000 | 0 | 11.86 | 377.1 | 777.3 | 933.1 |
| host: C 100/s | rep2 | 1999 | 1 | 1374.4 | 7358.2 | 10612.4 | 13546.6 |
| host: C 50/s | rep3 | 1000 | 0 | 11.13 | 46.71 | 209.7 | 287.2 |
| host: C 100/s | rep3 | 2000 | 0 | 7.81 | 196.1 | 545.4 | 900.3 |
| host: C 50/s | rep4 | 1000 | 0 | 10.59 | 17.98 | 116.8 | 177.9 |
| host: C 100/s | rep4 | 2000 | 0 | 7.39 | 10.78 | 26.27 | 63.99 |
| host: C 50/s | rep5 | 1000 | 0 | 10.56 | 15.14 | 211.8 | 337.0 |
| host: C 100/s | rep5 | 1999 | 1 | 4342.8 | 16659.0 | 20117.1 | 22403.1 |
| host: C 100/s | diag | 2000 | 0 | 7.60 | 11.52 | 14.90 | 26.97 |
| host: C 200/s | diag | 3999 | 1 | 42.68 | 16521.8 | 20236.2 | 21875.6 |
| in-network: A sequential | docker | 1000 | 0 | 3.17 | 4.40 | 5.72 | 14.48 |
| in-network: C 50/s | docker | 1000 | 0 | 10.27 | 14.91 | 122.3 | 230.6 |
| in-network: C 100/s | docker | 2000 | 0 | 6.63 | 8.37 | 17.55 | 93.18 |
| in-network: C 200/s | docker | 4000 | 0 | 5.59 | 33.97 | 337.4 | 666.2 |
| in-network: C 50/s | docker-rep1 | 1000 | 0 | 10.29 | 13.59 | 19.04 | 87.37 |
| in-network: C 100/s | docker-rep1 | 2000 | 0 | 7.07 | 11.15 | 93.66 | 208.2 |
| in-network: C 200/s | docker-rep1 | 4000 | 0 | 4.90 | 9.17 | 76.00 | 154.1 |
| in-network: C 50/s | docker-rep2 | 1000 | 0 | 10.07 | 13.15 | 21.99 | 51.85 |
| in-network: C 100/s | docker-rep2 | 2000 | 0 | 6.59 | 8.53 | 12.88 | 83.22 |
| in-network: C 200/s | docker-rep2 | 4000 | 0 | 6.26 | 81.68 | 401.2 | 774.1 |
| in-network: C 50/s | docker-rep3 | 1000 | 0 | 9.95 | 13.15 | 15.75 | 25.39 |
| in-network: C 100/s | docker-rep3 | 2000 | 0 | 6.74 | 11.52 | 217.3 | 426.3 |
| in-network: C 200/s | docker-rep3 | 4000 | 0 | 5.33 | 54.04 | 270.1 | 582.8 |
| in-network: C 50/s | docker-rep4 | 1000 | 0 | 9.74 | 13.35 | 23.75 | 38.57 |
| in-network: C 100/s | docker-rep4 | 2000 | 0 | 6.53 | 8.30 | 9.54 | 25.38 |
| in-network: C 200/s | docker-rep4 | 4000 | 0 | 5.76 | 4311.5 | 6965.0 | 9105.3 |
| in-network: C 200/s | docker-cgroup | 4000 | 0 | 4.60 | 12.09 | 195.2 | 406.4 |

## Runs with p99 under 100 ms

| Scenario | passes | runs |
|---|---|---|
| host: A sequential | 3 | 3 |
| host: B 1 clients (last) | 3 | 3 |
| host: B 10 clients (last) | 3 | 3 |
| host: B 100 clients (last) | 3 | 3 |
| host: C 10/s | 1 | 3 |
| host: C 50/s | 2 | 8 |
| host: C 100/s | 2 | 9 |
| host: C 200/s | 0 | 4 |
| host: D status path | 3 | 3 |
| in-network: A sequential | 1 | 1 |
| in-network: C 50/s | 4 | 5 |
| in-network: C 100/s | 4 | 5 |
| in-network: C 200/s | 1 | 6 |
