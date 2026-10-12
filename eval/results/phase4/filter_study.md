# Phase 4: metadata-filter study

Generated 2026-10-11T23:23:30.644603+00:00 by `eval/retrieval/filter_study.py`. No test-split labels used.

## 1. Unsafe items in the top-10: filters off vs on

Stress set: 12 allergens x 3 neutral templates + 5 dietary tags x 3, scope menu+docs, k=10. 'Unsafe' = a menu item that violates the filter, or a chunk that mentions such an item.

| chunk config | mode | filters | queries | queries with an unsafe hit | unsafe hits / hits | rate |
|---|---|---|---|---|---|---|
| fixed_128_o30 | vector | off | 51 | 46 | 161 / 510 | 31.6% |
| fixed_128_o30 | vector | on | 51 | 0 | 0 / 510 | 0.0% |
| fixed_128_o30 | hybrid | off | 51 | 45 | 117 / 510 | 22.9% |
| fixed_128_o30 | hybrid | on | 51 | 0 | 0 / 510 | 0.0% |
| recursive_256_o15 | vector | off | 51 | 47 | 174 / 510 | 34.1% |
| recursive_256_o15 | vector | on | 51 | 0 | 0 / 510 | 0.0% |
| recursive_256_o15 | hybrid | off | 51 | 45 | 163 / 510 | 32.0% |
| recursive_256_o15 | hybrid | on | 51 | 0 | 0 / 510 | 0.0% |

## 2. Cost of chunk over-linking (one allergen filter at a time, mean over the 12 allergens that appear on the menu)

`dropped` = share of the config's chunks removed by the filter; `mixed` = share of dropped chunks that ALSO mention a safe dish; `collateral loss` = share of dev evidence quotes that are themselves safe but lose every covering chunk.

| chunk config | chunks | mean tokens | chunks linked to a dish | mean dishes per chunk | dropped | mixed (of dropped) | collateral loss mean | max |
|---|---|---|---|---|---|---|---|---|
| fixed_128_o0 | 135 | 125.4 | 44.4% | 1.22 | 15.3% | 81.4% | 18.7% | 37.5% |
| fixed_128_o15 | 157 | 126.0 | 45.2% | 1.28 | 15.6% | 82.2% | 15.3% | 26.8% |
| fixed_128_o30 | 190 | 125.8 | 45.3% | 1.25 | 15.3% | 81.3% | 12.5% | 28.6% |
| fixed_128_o30_h | 218 | 114.5 | 42.7% | 1.08 | 14.0% | 77.3% | 12.3% | 28.6% |
| fixed_256_o0 | 69 | 245.3 | 52.2% | 2.32 | 23.8% | 94.7% | 28.5% | 46.4% |
| fixed_256_o15 | 79 | 249.4 | 50.6% | 2.42 | 24.6% | 95.6% | 30.2% | 44.6% |
| fixed_256_o30 | 95 | 250.2 | 52.6% | 2.37 | 24.6% | 95.1% | 27.7% | 39.3% |
| fixed_512_o0 | 36 | 470.2 | 63.9% | 4.22 | 31.7% | 94.1% | 39.0% | 51.8% |
| fixed_512_o15 | 42 | 469.0 | 59.5% | 4.26 | 31.6% | 94.4% | 39.3% | 55.4% |
| fixed_512_o30 | 48 | 487.4 | 66.7% | 4.75 | 33.2% | 94.5% | 35.3% | 51.8% |
| recursive_128_o15 | 205 | 100.9 | 39.5% | 0.94 | 12.2% | 74.5% | 13.6% | 32.1% |
| recursive_256_o15 | 103 | 200.0 | 46.6% | 1.83 | 19.6% | 93.2% | 25.7% | 44.6% |
| recursive_256_o15_h | 109 | 191.1 | 46.8% | 1.75 | 19.3% | 92.7% | 24.2% | 36.4% |
| recursive_512_o15 | 50 | 405.8 | 58.0% | 3.78 | 29.0% | 94.5% | 35.3% | 53.7% |
| sentence_256_o15 | 80 | 235.6 | 51.2% | 2.3 | 24.2% | 95.6% | 27.3% | 42.9% |
