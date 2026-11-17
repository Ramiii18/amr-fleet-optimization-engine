# Layout study: pre-registration (Phase 3, L7)

**Status: pre-registered on 2026-11-17 (`5bd94c6`), before any candidate layout was screened or
simulated; run as registered the same day. Results: see [Results](#results-added-after-the-runs-2026-11-17).**
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

---

# Results (added after the runs, 2026-11-17)

Everything below was computed after the pre-registration (`5bd94c6`) and follows it. Nothing above
was changed.

**Post-registration fix (path format only):** the experiment file first named the baseline with
Windows separators; the writer now uses forward slashes on every OS, and the same pre-registered
command regenerated `experiment.json` and `screening.json` with only that line changed. A re-run
reproduced every row and analysis value of `docs/results/layout_study.json` (0 of 17,300 differ).
No setting or result changed.

## What was run

- **Screening:** the pre-registered command, run once at `5bd94c6`, unchanged. It wrote
  `experiments/layout_study/candidates/` and stopped at no stopping case, because the spread set
  differs from the top three.

  | Layout | Station columns (dock wall) | Charger columns (opposite wall) | Station proxy, m per order | Charger proxy, m per trip |
  |---|---|---|---:|---:|
  | baseline | 4, 14, 24, 34 | 2, 5, 8 | 68.55 | 72.44 |
  | proxy-1 | 16, 18, 20, 22 | 2, 20, 38 | 62.15 | 58.76 |
  | proxy-2 | 18, 20, 22, 24 | 2, 20, 26 | 62.23 | 58.52 |
  | proxy-3 | 16, 18, 20, 24 | 2, 20, 26 | 62.34 | 58.52 |
  | spread | 2, 14, 26, 38 | 14, 26, 35 | 71.47 | 62.72 |

  As expected of a separable proxy, the proxy clusters all four stations in adjacent slots in the
  middle of the dock wall. The proxies of proxy-1 to proxy-3 lie within 0.2 m of each other.

- **The experiment** took 120 s with 8 workers, at `5bd94c6`. Every run is in
  `docs/results/layout_study.json`.

  ```powershell
  .venv\Scripts\python.exe -m amr_optimizer.lab experiments\layout_study\candidates\experiment.json `
      --workers 8 --out docs\results\layout_study.json
  ```

## Checks

- **Runs:** 400; with undelivered orders 0; with conflicts 0; longer than their disturbance
  schedule 0.
- **Pairing:** the pairing keys of the five layouts are equal for every family, policy and seed, so
  the lab paired every run.

## Primary tests: mean completion time, Holm over 12 tests, margin 5 s

The reading follows the pre-registered rules.
- **Better or worse:** the Holm-adjusted p is below 0.05 and the interval excludes zero.
- **Equivalent:** the whole interval lies within ±5 s.
- **Inconclusive:** otherwise.

| Family | Policy | Contrast | Mean (s) | Reference mean (s) | Difference [95% t interval] | Wins-losses | Holm p | Reading |
|---|---|---|---:|---:|---|---|---:|---|
| moderate-10 | greedy | proxy-1 vs spread | 116.1 | 121.1 | -5.0 [-7.5, -2.5] | 16-4 | 0.005 | **better** |
| moderate-10 | greedy | proxy-1 vs baseline | 116.1 | 122.1 | -6.0 [-9.1, -2.9] | 17-3 | 0.008 | **better** |
| moderate-10 | greedy | spread vs baseline | 121.1 | 122.1 | -0.9 [-4.5, +2.7] | 10-10 | 1.000 | **equivalent** |
| moderate-10 | cpsat-fair | proxy-1 vs spread | 116.7 | 119.1 | -2.5 [-5.9, +1.0] | 13-7 | 1.000 | inconclusive |
| moderate-10 | cpsat-fair | proxy-1 vs baseline | 116.7 | 120.2 | -3.5 [-7.2, +0.2] | 13-7 | 0.656 | inconclusive |
| moderate-10 | cpsat-fair | spread vs baseline | 119.1 | 120.2 | -1.0 [-5.5, +3.4] | 11-9 | 1.000 | inconclusive |
| high-10 | greedy | proxy-1 vs spread | 144.9 | 145.1 | -0.3 [-19.3, +18.8] | 14-6 | 1.000 | inconclusive |
| high-10 | greedy | proxy-1 vs baseline | 144.9 | 141.0 | +3.8 [-15.8, +23.5] | 16-4 | 1.000 | inconclusive |
| high-10 | greedy | spread vs baseline | 145.1 | 141.0 | +4.1 [-2.6, +10.8] | 11-9 | 1.000 | inconclusive |
| high-10 | cpsat-fair | proxy-1 vs spread | 131.9 | 131.6 | +0.3 [-10.9, +11.5] | 10-10 | 1.000 | inconclusive |
| high-10 | cpsat-fair | proxy-1 vs baseline | 131.9 | 130.6 | +1.3 [-9.4, +12.1] | 15-5 | 1.000 | inconclusive |
| high-10 | cpsat-fair | spread vs baseline | 131.6 | 130.6 | +1.1 [-8.4, +10.5] | 12-8 | 1.000 | inconclusive |

**The answer to the question, per combination:**

| Family | Policy | Proxy-best against spread | Proxy-best against baseline | Spread against baseline |
|---|---|---|---|---|
| moderate-10 | greedy | **holds** (better by 5.0 s) | better by 6.0 s | equivalent |
| moderate-10 | cpsat-fair | inconclusive | inconclusive | inconclusive |
| high-10 | greedy | inconclusive | inconclusive | inconclusive |
| high-10 | cpsat-fair | inconclusive | inconclusive | inconclusive |

- **The static proxy's choice holds in one of the four combinations:** moderate load with greedy
  dispatch. There, the clustered stations beat both the spread control and the hand-made baseline
  by 5 to 6 s (about 4 to 5%).
- **In the other three, 20 seeds show no difference between any of the three placements.** The
  proxy's choice is neither confirmed nor refuted there.
  - At moderate load with `cpsat-fair`, the intervals are narrow (about ±4 s) and lean the same
    way, but they do not clear zero after correction.
  - At high load the intervals are wide (±7 to ±20 s), so differences of the size seen at moderate
    load could not be resolved.
- **Nowhere did the proxy's choice lose on the primary KPI.** The congestion that clustering might
  cause did not show up in mean completion time in any combination.

## Secondary: does the proxy rank the five layouts as the simulation does?

Kendall's tau-b between the proxy ranking and the ranking by mean completion time, with a 95%
percentile interval from 10,000 resamples of seeds. With five layouts this is descriptive only.

```powershell
.venv\Scripts\python.exe scripts\layout_study_tau.py docs\results\layout_study.json `
    experiments\layout_study\candidates\screening.json
```

| Family | Policy | Layouts by proxy (best first) | Layouts by simulation (best first) | tau-b | 95% bootstrap interval |
|---|---|---|---|---:|---|
| moderate-10 | greedy | proxy-1 < proxy-2 < proxy-3 < baseline < spread | proxy-1 < proxy-3 < proxy-2 < spread < baseline | +0.60 | [+0.20, +0.80] |
| moderate-10 | cpsat-fair | proxy-1 < proxy-2 < proxy-3 < baseline < spread | proxy-3 < proxy-2 < proxy-1 < spread < baseline | +0.20 | [+0.00, +0.60] |
| high-10 | greedy | proxy-1 < proxy-2 < proxy-3 < baseline < spread | proxy-3 < proxy-2 < baseline < proxy-1 < spread | +0.20 | [-0.20, +0.80] |
| high-10 | cpsat-fair | proxy-1 < proxy-2 < proxy-3 < baseline < spread | proxy-3 < baseline < proxy-2 < spread < proxy-1 | -0.20 | [-0.60, +0.60] |

- **The rankings broadly agree only at moderate load with greedy dispatch** (+0.60). Elsewhere the
  rank correlation is weak or negative, and every interval is wide.
- **The proxy separates its own top three by 0.2 m per order,** far below what the simulation can
  resolve, so their order in the simulated ranking is noise. Much of tau is that noise.

## Exploratory (not corrected; post hoc where it explains)

All of these come from `docs/results/layout_study.json`, through the lab's report.

- **proxy-3 against baseline:** better in 3 of 4 combinations by the lab's interval verdict,
  without correction:
  - moderate, greedy: -5.9 s [-9.5, -2.3];
  - moderate, `cpsat-fair`: -6.0 s [-10.0, -2.1];
  - high, greedy: -8.5 s [-15.8, -1.2];
  - high, `cpsat-fair`: -8.2 s [-17.1, +0.7], which is inconclusive.

  proxy-3 keeps one station apart from the other three (columns 16, 18, 20, 24). It was not a
  primary test, and its proxy differs from proxy-1's by 0.2 m.
- **The worst case is longer with clustered stations at moderate load.** proxy-1 against baseline,
  `completion_max_s`, without correction:
  - greedy: +37.1 s [+4.0, +70.2];
  - `cpsat-fair`: +48.4 s [+1.6, +95.1].

  This fits the expectation that clustered stations congest. But the rows do not record queueing
  per station, so the cause is not established. At high load the same difference is inconclusive.
- **Charging was mostly topping up.**
  - Robots made about 32 opportunistic top-ups per run but only 0.44 trips because a battery ran
    low.
  - Waits for a charger occurred in 10 of 400 runs, at most 3.8 s on average for any layout.
  - Time out of service averaged at most 3.3% per layout, and reached 14.7% in a single run.
  - The exploratory charger KPIs show no clear difference between layouts, so **this study gives
    little evidence about charger placement**, even though the chargers differed in every
    candidate.

## What this shows, and what it does not

- **One synthetic hall, one fleet size, two loads, 20 seeds.** The answer holds for this hall and
  these conditions only.
- **The headline:** on this hall the cheap static proxy picked a station placement that was never
  worse in mean completion time than the spread control or the hand-made baseline, and was better
  by 5 to 6 s at moderate load under greedy dispatch. Elsewhere, 20 seeds could not tell the
  placements apart. Clustering showed its cost only in the worst case at moderate load, and only
  in exploratory tests.
- **Charger placement is barely informed by this study:** low-battery charging was rare, and the
  charger KPIs show no clear difference.
- **The generated experiment file names the baseline with a Windows path**
  (`..\..\..\layouts\study\baseline.layout.json`), as the screening command was given it. It runs
  on Windows; on Linux the path would need forward slashes.
