# Layout study: pre-registration (Phase 3, L7)

**Status: pre-registered on 2026-11-17, before any candidate layout was screened or simulated.**
Everything below is fixed by this commit. Anything decided after the runs will be labelled as
added afterwards. The results will be added to this document in a later commit, whatever they are.

## The question

On one synthetic hall: **does the static proxy pick the station placement that the simulation
with traffic prefers?** And do the screened placements beat what a person would draw by hand?

## The hall and the baseline (committed before this plan, `7efc7c9`)

- `layouts/study/hall.base.layout.json`: a synthetic 40 × 30 m hall at 1 m cells.
  - 13 one-way aisles, 400 storage cells, cross aisles at the top and in the middle, and a
    three-lane dock zone.
  - 19 station slots along the dock wall (row 28) and 13 charger slots in the opposite wall
    (row 1).
  - 12 parking niches in the left wall, so every robot of the fleet of 10 starts on parking and
    moving a charger never moves a robot's start (D41).
- `layouts/study/baseline.layout.json`: what a person would draw by hand.
  - Four stations evenly spaced along the dock wall, in columns 4, 14, 24 and 34.
  - Three chargers together in the top-left corner, in columns 2, 5 and 8.

## The candidates (screening, D43, D44)

This command will be run once, unchanged, after the owner approves this plan:

```powershell
.venv\Scripts\python.exe -m amr_optimizer.layout screen layouts\study\hall.base.layout.json `
    --stations 4 --chargers 3 --top 3 --station-rows 28 --charger-rows 1 `
    --out experiments\layout_study\candidates `
    --baseline ..\..\..\layouts\study\baseline.layout.json `
    --template experiments\layout_study\template.json
```

- **The five layouts:**
  - `proxy-1`, `proxy-2` and `proxy-3`: the three best station sets by the round-trip proxy;
  - `spread`: the station set that maximises the smallest round trip between two stations;
  - `baseline`.

  Each screened candidate gets its chargers from the CP-SAT p-median given its stations. All five
  layouts keep the same storage cells and the same station and charger counts, so the lab pairs
  them by seed (D41).
- **If the spread set equals one of the top three**, no `spread` file is written and the named
  contrasts cannot be formed. In that case I stop and report, and nothing is run.

## The conditions (`experiments/layout_study/template.json`)

- **Fleet and loads:** 10 robots. Two families, at 130 and 190 orders per hour (pilot rule, below),
  with 1800 s of orders.
- **Policies:** `greedy` and `cpsat-fair`, with traffic always on (the lab).
- **Seeds:** 0 to 19, the unit of replication. The pilot used 100 to 104, so they never overlap.
- **Disturbances:** the schedule model (D36).
  - Delays: 10% of the time slow per corridor, by 1 to 4 s, in episodes of 30 to 120 s.
  - Breakdowns: one per robot every 1200 s on average, repaired in 30 to 180 s, with recovery by
    replanning.
- **Batteries:** the default synthetic battery, with `opportunistic` charging (D26, D27).
- **The run:** 5 layouts × 2 policies × 2 families × 20 seeds = 400 runs.

## Primary tests (owner's decision, 2026-11-17)

- **The KPI:** `completion_mean_s`, the mean time from an order's release to its delivery, in
  seconds. Lower is better.
- **Three pairs**, each per policy and family, which makes 12 tests:
  1. `proxy-1` against `spread`;
  2. `proxy-1` against `baseline`;
  3. `spread` against `baseline`.
- **Statistics, as the lab computes them:**
  - the paired difference over the 20 seeds, with a 95% t interval;
  - Holm correction over the 12 tests;
  - an equivalence margin of 5 s.
- **How a test is read:**
  - **Better or worse:** the Holm-adjusted p-value is below 0.05, and the interval excludes zero.
  - **Equivalent:** the whole 95% interval lies within ±5 s.
  - **Inconclusive:** neither of the two.
- **The answer to the question**, given separately for each of the four policy × family
  combinations:
  - **The proxy's choice holds** if `proxy-1` is better than `spread`, or equivalent to it.
  - **The proxy's choice fails** if `proxy-1` is worse than `spread`.
  - Otherwise it is inconclusive.

  The comparisons with `baseline` are read the same way.

## Secondary and exploratory analyses

- **Secondary: Kendall's tau-b** between the static ranking and the simulated ranking of the five
  layouts, per policy and family.
  - The static ranking is by the station round-trip proxy from `screening.json`; the baseline's
    proxy comes from `proxies()`.
  - The simulated ranking is by mean completion time over the 20 seeds.
  - It comes with a 95% percentile interval from 10,000 bootstrap resamples of whole seeds
    (generator seed 0).
  - With five layouts, tau takes few distinct values; it is reported as descriptive, not tested.
- **Exploratory, not corrected for multiple comparisons:**
  - `proxy-2` and `proxy-3` against `baseline`;
  - the other KPIs: `completion_p95_s`, `completion_max_s`, `charger_wait_s` and
    `out_of_service_share`.
  - To explain where a placement loses, the waiting and charging figures the rows record. Any such
    explanation is post hoc.

## Checks on the runs

- Every run must deliver every order with 0 conflicts. A run that does not is reported, not
  dropped.
- The lab refuses to compare runs whose pairing keys differ. If it does, I stop and report.
- Runs longer than their disturbance schedule (`beyond_horizon`) are counted and reported.

## What will not change after the runs

The hall, the baseline, the screening command, the conditions, the seeds, the primary KPI, the
pairs, the margin, the correction and the reading rules above. A change would need a new
pre-registration, and the original analysis would still be reported.

## The pilot that set the loads (baseline only)

```powershell
.venv\Scripts\python.exe scripts\pilot_capacity.py layouts\study\baseline.layout.json `
    --conditions experiments\layout_study\template.json --robots 10 `
    --loads 100 150 200 250 300 400 --workers 8 --out docs\results\layout_study_pilot.json
```

- **Setup:** 60 runs on the baseline at code `7efc7c9` plus the L7 tooling working tree, 50 s with
  8 workers, on pilot seeds 100 to 104. Means over the seeds:

| Orders/h | Policy | Throughput /h | Completion mean s | P95 s | All delivered | Out of service |
|---:|---|---:|---:|---:|---|---:|
| 100 | greedy | 81.1 | 130.9 | 271.1 | 5/5 | 0.0% |
| 100 | cpsat-fair | 82.6 | 128.9 | 265.3 | 5/5 | 0.0% |
| 150 | greedy | 131.6 | 130.5 | 279.0 | 5/5 | 0.0% |
| 150 | cpsat-fair | 133.2 | 127.8 | 254.5 | 5/5 | 0.0% |
| 200 | greedy | 176.8 | 157.9 | 304.5 | 5/5 | 3.0% |
| 200 | cpsat-fair | 179.7 | 143.4 | 269.4 | 5/5 | 2.0% |
| 250 | greedy | 189.3 | 236.1 | 449.5 | 5/5 | 5.9% |
| 250 | cpsat-fair | 200.7 | 150.7 | 278.3 | 5/5 | 4.0% |
| 300 | greedy | 198.6 | 364.0 | 721.5 | 5/5 | 12.6% |
| 300 | cpsat-fair | 219.8 | 256.3 | 509.7 | 5/5 | 10.3% |
| 400 | greedy | 204.9 | 782.7 | 1529.5 | 5/5 | 14.8% |
| 400 | cpsat-fair | 224.0 | 626.9 | 1231.0 | 5/5 | 13.4% |

- **The rule, fixed in the script before the pilot ran (D44):**
  - The capacity C is the throughput of `cpsat-fair` at the highest load, so C = 224.0 orders per
    hour.
  - The families run at 0.60 C and 0.85 C, which is **130 and 190 orders per hour**.
- **Reading the pilot:**
  - At 190 orders/h the fleet is just below the knee: completion time starts to rise at 200
    orders/h.
  - "Throughput" is lower than the load at light load, because it counts the run until the last
    delivery.
  - Per-run rows are in `docs/results/layout_study_pilot.json`.
