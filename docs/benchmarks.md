# Benchmarks

Every result below is the unchanged output of a script in `scripts/`. Runtimes depend on the machine;
node-expansion counts do not.

## Routing: Dijkstra vs A* (M1, re-run 2026-11-04)

**Command:** `.venv\Scripts\python.exe scripts\compare_routing.py` with the defaults: 300 queries per map,
5 repeats per query, seed 42.

**Machine:** MSI Thin GF63 12UCX, Intel Core i7-12650H, 32 GB RAM, Windows 11, on AC power. The CPU
model name comes from Windows; Python only reports the family and stepping string shown below.

**Queries:** random pairs of non-intersection locations (storage, stations, chargers, parking) on the
generated presets, identical for all algorithms. Runtime per query is the fastest of 5 runs; the
table shows the median and P95 of those values across queries.

```text
Routing comparison, 2026-11-04
Environment: Python 3.13.1, Windows-11-10.0.26200-SP0, CPU Intel64 Family 6 Model 154 Stepping 3, GenuineIntel, amr-fleet-optimization-engine 0.0.1 (commit 8d02166)
Settings: 300 queries per map, 5 repeats per query, seed 42

| Map | Nodes | Edges | Algorithm | Mean expanded | P95 expanded | Median µs | P95 µs |
|---|---:|---:|---|---:|---:|---:|---:|
| small | 45 | 74 | Dijkstra | 26.8 | 44 | 14.2 | 21.4 |
| small | 45 | 74 | A* Euclidean | 17.6 | 33 | 12.9 | 24.0 |
| small | 45 | 74 | A* Manhattan | 15.9 | 32 | 11.6 | 22.4 |
| medium | 179 | 296 | Dijkstra | 94.1 | 172 | 50.8 | 89.9 |
| medium | 179 | 296 | A* Euclidean | 43.0 | 113 | 26.9 | 82.9 |
| medium | 179 | 296 | A* Manhattan | 33.3 | 88 | 20.4 | 68.9 |
| large | 461 | 736 | Dijkstra | 241.1 | 433 | 135.6 | 232.7 |
| large | 461 | 736 | A* Euclidean | 101.3 | 257 | 60.8 | 191.6 |
| large | 461 | 736 | A* Manhattan | 71.7 | 199 | 35.4 | 156.6 |

All algorithms returned identical route lengths for every query.
```

### Reading the results

- **Correctness first.** All three algorithms returned the same route length for all 900 queries.
- **Expansions.** On the large map, A* with Manhattan distance settles 3.4× fewer nodes than Dijkstra on
  average (71.7 vs 241.1), and A* with Euclidean distance 2.4× fewer. Manhattan is the tighter estimate
  because every edge in these maps is axis-aligned: no route can be shorter than |dx| + |dy|.
- **Runtime follows expansions, but less than proportionally.** Each A* expansion costs more than a
  Dijkstra one: a heuristic call for every queued neighbour, and a wider heap entry. On the large map,
  A* Manhattan is 3.8× faster at the median (35.4 vs 135.6 µs).
- **On the small map the gain almost disappears.** A* Euclidean's P95 is even slightly worse than
  Dijkstra's (24.0 vs 21.4 µs). With 45 nodes the search is so short that the heuristic's overhead weighs
  as much as the expansions it saves.
- **The worst cases (P95) improve less than the typical case (median).** The heuristic doesn't know which
  way the aisles run. When a target is against an aisle's direction, the route has to detour through a
  cross aisle, and A* ends up exploring much of what Dijkstra explores.
- **Noise.** Three runs of the same code (two on 2026-11-03, this one on 2026-11-04) differed by up to
  about 8% in runtime; for example, the large-map Dijkstra median was 135.1, 143.2 and 135.6 µs.
  Expansion counts were identical every time.

### What this does not show

- This is single-robot, static routing: no other robots, no time dimension, no congestion. It is a
  building block, not a fleet-level result.
- The microsecond figures are for pure Python on one laptop, so they are mainly meaningful relative to
  each other. Routing cost inside the dispatcher (M2), and space-time search, which makes the search
  space much larger (M4), will be measured separately.

## Dispatch: greedy vs optimal assignment, in simulation (M2, 2026-11-04)

**Command:** `.venv\Scripts\python.exe scripts\compare_dispatch.py` with the defaults: 20 seeds, orders
released for one hour.

**Machine:** the same laptop as above. Only the final line ("320 runs in ...") depends on the machine.
The KPIs are simulated time, fully determined by the code and the seeds.

**Setup.**
- The large preset (461 nodes).
- Robots at 1 m/s, 0.1 s ticks, 10 s loading and 10 s unloading.
- Retrieval orders from uniformly random storage locations to uniformly random stations, arriving as a
  Bernoulli process (D14).
- Four variants on exactly the same 20 scenarios per configuration: greedy and Hungarian dispatch, each
  deciding at every event or in 10 s decision rounds.
- The last column is the paired difference in mean completion time against greedy at every event, with
  a 95% t confidence interval over the 20 seeds.

```text
Dispatch comparison, 2026-11-04
Environment: Python 3.13.1, Windows-11-10.0.26200-SP0, numpy 2.5.4, scipy 1.18.1, amr-fleet-optimization-engine 0.0.1 (commit e038fb9)
Settings: map large, orders released for 3600 s, seeds 0-19, 1 m/s, 0.1 s ticks, 10 s loading and 10 s unloading

| Robots | Orders/h | Policy | Decisions | Utilization | Waiting s | Assign delay s | Completion s | Completion P95 s | Empty share | Completion diff. vs greedy at every event (95% CI) |
|---:|---:|---|---|---:|---:|---:|---:|---:|---:|---|
| 5 | 100 | greedy | every event | 60.9% | 51.6 | 8.6 | 119.5 | 180.7 | 47.3% | baseline |
| 5 | 100 | hungarian | every event | 60.9% | 51.6 | 8.6 | 119.5 | 180.7 | 47.3% | +0.0 s [+0.0, +0.0] |
| 5 | 100 | greedy | 10 s rounds | 60.6% | 57.6 | 15.0 | 125.5 | 188.9 | 47.1% | +6.0 s [+5.4, +6.6] |
| 5 | 100 | hungarian | 10 s rounds | 60.5% | 57.5 | 15.0 | 125.4 | 189.8 | 47.0% | +5.9 s [+5.3, +6.6] |
| 5 | 150 | greedy | every event | 89.5% | 144.5 | 98.1 | 212.0 | 357.4 | 49.4% | baseline |
| 5 | 150 | hungarian | every event | 89.5% | 144.5 | 98.1 | 212.0 | 357.4 | 49.4% | +0.0 s [+0.0, +0.0] |
| 5 | 150 | greedy | 10 s rounds | 87.9% | 168.8 | 123.0 | 236.3 | 396.1 | 49.1% | +24.3 s [+17.8, +30.9] |
| 5 | 150 | hungarian | 10 s rounds | 87.8% | 167.3 | 121.6 | 234.8 | 395.0 | 49.1% | +22.8 s [+17.4, +28.3] |
| 10 | 200 | greedy | every event | 57.7% | 41.6 | 1.8 | 108.9 | 150.5 | 45.7% | baseline |
| 10 | 200 | hungarian | every event | 57.7% | 41.6 | 1.8 | 108.9 | 150.5 | 45.7% | +0.0 s [+0.0, +0.0] |
| 10 | 200 | greedy | 10 s rounds | 57.6% | 46.7 | 7.0 | 114.0 | 156.2 | 45.6% | +5.1 s [+4.9, +5.3] |
| 10 | 200 | hungarian | 10 s rounds | 57.5% | 46.5 | 7.0 | 113.8 | 157.0 | 45.5% | +4.9 s [+4.7, +5.1] |
| 10 | 300 | greedy | every event | 90.8% | 114.8 | 68.4 | 182.4 | 285.0 | 49.3% | baseline |
| 10 | 300 | hungarian | every event | 90.8% | 114.4 | 68.1 | 182.1 | 284.3 | 49.3% | -0.4 s [-0.9, +0.1] |
| 10 | 300 | greedy | 10 s rounds | 89.1% | 132.5 | 87.4 | 200.2 | 314.8 | 48.6% | +17.8 s [+13.5, +22.1] |
| 10 | 300 | hungarian | 10 s rounds | 88.9% | 129.4 | 84.7 | 197.1 | 311.8 | 48.4% | +14.7 s [+11.1, +18.2] |

320 runs in 23.2 s.
Every order was delivered in every run.
```

### Reading the results

- **Optimal matching gives no measurable gain when decisions happen at every event.** In three of the
  four configurations, Hungarian and greedy produce *identical* runs. In the fourth (10 robots, 300
  orders/h) the difference is −0.4 s, with a confidence interval of [−0.9, +0.1] s that includes zero.
  This is the expected outcome, not a bug. A diagnostic run on 5 seeds counted the decisions where both
  sides had at least two candidates. At 5 robots and 150 orders/h, none of 734 policy calls did. At 10
  robots and 300 orders/h, 7 of 1,471 did, and only one of those decisions differed. With one robot or
  one order, the optimal assignment *is* the greedy choice.
- **Decision rounds make both policies worse.** 10 s rounds add 5–6 s of mean completion time at
  moderate load, close to the expected half-interval of waiting. At high load they add 18–24 s.
- **The extra cost at high load comes from lost capacity.** A robot that becomes free between rounds
  sits idle until the next round. With a queue waiting, that idle time delays every order behind it,
  and utilization drops slightly (89.5% to 87.9% at 5 robots, 150 orders/h) while waiting grows.
- **Within rounds, Hungarian is slightly better than greedy**, because rounds create decisions with
  several robots and several orders. At 10 robots and 300 orders/h it recovers 3.1 s: +14.7 s against
  +17.8 s. The gain is far smaller than the cost of batching.
- **Empty travel barely changes.** The empty share stays at 45–49% in every variant; rounds lower it by
  at most 0.9 percentage points. Half of all driving is empty travel to pickups. That is the share
  dispatch could reduce, and per-decision matching does not reach it.

### What this means for M3

In this model (one order per robot at a time, identical robots, uniformly random locations, no
congestion), the dispatch decision is almost always trivial. Optimization has to change the *problem*,
not just solve the same decision better. Candidates for the CP-SAT phase:
- **look-ahead:** assign orders to busy robots that will finish soon, near the pickup;
- **sequencing several orders per robot** over a planning horizon;
- **time windows and priorities**, where the greedy rule has no principled answer.

### What this does not show

- No congestion. Robots never block each other (M4), so these completion times are optimistic, and
  spreading robots out has no value in this model.
- One map, one layout and uniform demand. Skewed demand (popular storage zones) or clustered stations
  could change the picture, and are untested.
- Twenty seeds per configuration. The confidence intervals show the resulting uncertainty; differences
  smaller than about 1 s cannot be resolved.
- The runs are deterministic, and the scenario generator is checked on Windows and Linux by CI. This
  table itself was produced on Windows only.

## Planning: greedy vs insertion vs CP-SAT, in simulation (M3, 2026-11-04)

**Command:** `.venv\Scripts\python.exe scripts\compare_planning.py --jobs 8` with the defaults: 20 seeds,
orders released for one hour.

**Machine:** the same laptop as above. The KPIs are simulated time, fully determined by the code and the
seeds, and do not depend on the 8 parallel processes (D21). The solve times and the final line ("240
runs in ...") do depend on the machine, and on the processes sharing the CPU.

**Setup.**
- The same map, robot speed, handling times, configurations and seeds as the M2 comparison above.
- Three variants on exactly the same 20 scenarios per configuration, all deciding at every event:
  greedy (the M2 baseline), insertion (D19) and CP-SAT (D20), the last two planning every robot and the
  oldest 10 orders.
- Paired differences in mean completion time against greedy, and for CP-SAT against insertion, with 95%
  t confidence intervals over the 20 seeds.
- "Completion max s" is the mean over seeds of each run's worst completion time.

```text
Planning comparison, 2026-11-04
Environment: Python 3.13.1, Windows-11-10.0.26200-SP0, ortools 9.15.6755, amr-fleet-optimization-engine 0.0.1 (commit 551d256), 8 worker processes
Settings: map large, orders released for 3600 s, seeds 0-19, 1 m/s, 0.1 s ticks, 10 s loading and 10 s unloading; planning window 10 orders; CP-SAT: 1 worker, deterministic limit 0.2, seed 0

| Robots | Orders/h | Policy | Utilization | Waiting s | Completion s | Completion P95 s | Completion max s | Empty share | Completion diff. vs greedy (95% CI) | vs insertion (95% CI) |
|---:|---:|---|---:|---:|---:|---:|---:|---:|---|---|
| 5 | 100 | greedy | 60.9% | 51.6 | 119.5 | 180.7 | 216.0 | 47.3% | baseline |  |
| 5 | 100 | insertion | 60.0% | 50.4 | 118.3 | 176.9 | 212.9 | 46.3% | -1.2 s [-1.9, -0.5] |  |
| 5 | 100 | cpsat | 59.7% | 49.6 | 117.5 | 176.2 | 221.3 | 46.0% | -2.0 s [-2.9, -1.1] | -0.8 s [-1.2, -0.4] |
| 5 | 150 | greedy | 89.5% | 144.5 | 212.0 | 357.4 | 414.9 | 49.4% | baseline |  |
| 5 | 150 | insertion | 87.5% | 105.4 | 172.9 | 356.4 | 520.6 | 46.9% | -39.1 s [-55.5, -22.7] |  |
| 5 | 150 | cpsat | 86.6% | 93.7 | 161.2 | 325.7 | 625.0 | 45.8% | -50.8 s [-70.5, -31.0] | -11.7 s [-15.5, -7.9] |
| 10 | 200 | greedy | 57.7% | 41.6 | 108.9 | 150.5 | 190.2 | 45.7% | baseline |  |
| 10 | 200 | insertion | 56.7% | 40.3 | 107.6 | 147.4 | 179.8 | 44.5% | -1.3 s [-1.6, -0.9] |  |
| 10 | 200 | cpsat | 56.6% | 40.1 | 107.5 | 147.1 | 180.4 | 44.4% | -1.4 s [-1.8, -1.0] | -0.2 s [-0.3, +0.0] |
| 10 | 300 | greedy | 90.8% | 114.8 | 182.4 | 285.0 | 342.4 | 49.3% | baseline |  |
| 10 | 300 | insertion | 87.8% | 79.2 | 146.9 | 243.2 | 303.0 | 46.2% | -35.6 s [-48.2, -23.0] |  |
| 10 | 300 | cpsat | 86.5% | 67.8 | 135.5 | 224.1 | 368.3 | 45.0% | -47.0 s [-63.6, -30.3] | -11.4 s [-15.6, -7.2] |

CP-SAT solver statistics, over all decisions of all seeds:

| Robots | Orders/h | Decisions | Searched | Proven optimal | Stopped at limit | Fallback | Improved on insertion | Mean gap when stopped | Solve s mean / P95 / max |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| 5 | 100 | 2434 | 302 | 302 (100.0%) | 0 (0.0%) | 0 (0.0%) | 112 | - | 0.009 / 0.020 / 0.093 |
| 5 | 150 | 3367 | 1856 | 1579 (85.1%) | 277 (14.9%) | 0 (0.0%) | 1113 | 36.4% | 0.318 / 1.739 / 2.188 |
| 10 | 200 | 5213 | 485 | 485 (100.0%) | 0 (0.0%) | 0 (0.0%) | 97 | - | 0.052 / 0.087 / 0.203 |
| 10 | 300 | 7687 | 3915 | 3317 (84.7%) | 598 (15.3%) | 0 (0.0%) | 1967 | 22.3% | 0.318 / 1.608 / 3.693 |

240 runs in 304.5 s.
Every order was delivered in every run.
```

### Reading the results

- **The greedy rows reproduce the M2 table exactly**: the same code and seeds give the same runs.
- **At high load, planning pays off strongly.** At about 90% utilization (5 robots at 150 orders/h, 10
  robots at 300 orders/h):
  - insertion cuts mean completion time by 39.1 s and 35.6 s against greedy, 18–20%;
  - CP-SAT cuts it by 50.8 s and 47.0 s, 24–26%;
  - mean waiting falls from 144.5 s to 93.7 s, and from 114.8 s to 67.8 s.
- **At moderate load, the gain is small but measurable:** 1.2 to 2.0 s, with intervals that exclude zero.
  When idle robots are usually available, there is little to plan.
- **CP-SAT earns its place over insertion in three of the four configurations** (D19 criterion):
  - −11.7 s [−15.5, −7.9] at 5 robots and 150 orders/h;
  - −11.4 s [−15.6, −7.2] at 10 robots and 300 orders/h;
  - −0.8 s [−1.2, −0.4] at 5 robots and 100 orders/h.
  At 10 robots and 200 orders/h the difference is −0.2 s and its interval reaches zero, so no difference
  is resolved there.
- **The gain comes from less empty travel.** The empty share falls from 49.4% to 45.8% at 5 robots and
  150 orders/h. The same orders are delivered with less driving, utilization drops by 3–4 percentage
  points, and the spare capacity shortens the queue.
- **The price is the worst case.** The maximum completion time rises while the mean and the P95 fall:
  - at 5 robots and 150 orders/h: 414.9 s for greedy, 520.6 s for insertion, 625.0 s for CP-SAT;
  - at 10 robots and 300 orders/h: 342.4 s for greedy, 303.0 s for insertion, 368.3 s for CP-SAT.

  A few distant orders keep being sequenced last, which is the starvation risk named in D18. The
  window of the oldest 10 orders limits it, but does not prevent it. CP-SAT, the stronger optimiser,
  has the worst maximum in both high-load configurations. That fits the objective: total completion
  time is indifferent to how the waiting is distributed, so it accepts a few long waits in exchange for
  many shorter ones.
- **Solver behaviour.**
  - At moderate load, every searched decision was proven optimal.
  - At high load, 15% of searched decisions stopped at the limit, with mean remaining gaps of 36% and
    22%. The lower bounds are weak (algorithms section 11.3), so these gaps say little about how far
    the plans actually are from optimal. In the development probe, plans at this limit matched the best
    plan found by any setting in 157 of 165 decisions.
  - There was no fallback to the insertion plan.
  - Solve times: 0.32 s mean, 1.6–1.7 s P95 and 3.7 s maximum at high load, measured with 8 processes
    sharing the CPU.

### What this means for M3

The planning model is the main win, and exact search adds about another 11 s at high load. The
worst-case completion time, however, gets worse. Increment 5 of the M3 plan was conditional on exactly
this: it adds due times, so that the objective cares about the orders that have waited longest.

### What this does not show

- No congestion. Robots never block each other (M4), and the availability of busy robots is predicted
  exactly. With traffic, both the predictions and the value of look-ahead may change.
- The worst case is reported as a mean of per-run maxima, without a paired interval.
- One map, uniform demand and 20 seeds, as in M2.
- Solve times were measured under parallel load on one laptop. A real-time system would need wall-clock
  limits, and with them plans would no longer be reproducible.
- This table was produced on Windows only.

## Planning with the worst-first objective (M3.5, 2026-11-05)

**Command:** `.venv\Scripts\python.exe scripts\compare_planning.py --jobs 8`, as for M3.4, with the two
worst-first variants added (D22).

**Setup.** As in the M3.4 section above, plus `insertion-fair` and `cpsat-fair`. These minimise the
longest lead time first and the total completion time second. Two columns are new:
- the paired difference of each CP-SAT variant against insertion with the *same* objective;
- the paired difference of the worst-case completion time against greedy.

```text
Planning comparison, 2026-11-05
Environment: Python 3.13.1, Windows-11-10.0.26200-SP0, ortools 9.15.6755, amr-fleet-optimization-engine 0.0.1 (commit c47eaaf), 8 worker processes
Settings: map large, orders released for 3600 s, seeds 0-19, 1 m/s, 0.1 s ticks, 10 s loading and 10 s unloading; planning window 10 orders; CP-SAT: 1 worker, deterministic limit 0.2, seed 0

| Robots | Orders/h | Policy | Utilization | Waiting s | Completion s | Completion P95 s | Completion max s | Empty share | Completion diff. vs greedy (95% CI) | vs insertion, same objective (95% CI) | Max diff. vs greedy (95% CI) |
|---:|---:|---|---:|---:|---:|---:|---:|---:|---|---|---|
| 5 | 100 | greedy | 60.9% | 51.6 | 119.5 | 180.7 | 216.0 | 47.3% | baseline |  | baseline |
| 5 | 100 | insertion | 60.0% | 50.4 | 118.3 | 176.9 | 212.9 | 46.3% | -1.2 s [-1.9, -0.5] |  | -3.1 s [-9.4, +3.2] |
| 5 | 100 | cpsat | 59.7% | 49.6 | 117.5 | 176.2 | 221.3 | 46.0% | -2.0 s [-2.9, -1.1] | -0.8 s [-1.2, -0.4] | +5.3 s [-1.4, +12.0] |
| 5 | 100 | insertion-fair | 60.0% | 50.4 | 118.3 | 175.0 | 209.6 | 46.3% | -1.2 s [-1.9, -0.6] |  | -6.5 s [-11.6, -1.3] |
| 5 | 100 | cpsat-fair | 59.9% | 50.6 | 118.5 | 176.7 | 202.3 | 46.2% | -1.0 s [-1.7, -0.3] | +0.2 s [-0.1, +0.6] | -13.7 s [-20.0, -7.5] |
| 5 | 150 | greedy | 89.5% | 144.5 | 212.0 | 357.4 | 414.9 | 49.4% | baseline |  | baseline |
| 5 | 150 | insertion | 87.5% | 105.4 | 172.9 | 356.4 | 520.6 | 46.9% | -39.1 s [-55.5, -22.7] |  | +105.7 s [+62.6, +148.8] |
| 5 | 150 | cpsat | 86.6% | 93.7 | 161.2 | 325.7 | 625.0 | 45.8% | -50.8 s [-70.5, -31.0] | -11.7 s [-15.5, -7.9] | +210.1 s [+100.4, +319.7] |
| 5 | 150 | insertion-fair | 88.1% | 122.2 | 189.7 | 321.6 | 378.9 | 47.7% | -22.3 s [-30.4, -14.1] |  | -36.0 s [-49.8, -22.3] |
| 5 | 150 | cpsat-fair | 87.7% | 112.9 | 180.4 | 304.2 | 334.6 | 47.0% | -31.6 s [-46.0, -17.1] | -9.3 s [-17.1, -1.5] | -80.3 s [-102.0, -58.7] |
| 10 | 200 | greedy | 57.7% | 41.6 | 108.9 | 150.5 | 190.2 | 45.7% | baseline |  | baseline |
| 10 | 200 | insertion | 56.7% | 40.3 | 107.6 | 147.4 | 179.8 | 44.5% | -1.3 s [-1.6, -0.9] |  | -10.4 s [-16.2, -4.6] |
| 10 | 200 | cpsat | 56.6% | 40.1 | 107.5 | 147.1 | 180.4 | 44.4% | -1.4 s [-1.8, -1.0] | -0.2 s [-0.3, +0.0] | -9.8 s [-16.3, -3.3] |
| 10 | 200 | insertion-fair | 56.7% | 40.4 | 107.7 | 147.7 | 180.0 | 44.5% | -1.2 s [-1.6, -0.8] |  | -10.2 s [-16.2, -4.2] |
| 10 | 200 | cpsat-fair | 56.7% | 40.4 | 107.7 | 147.0 | 173.5 | 44.5% | -1.2 s [-1.6, -0.8] | +0.0 s [-0.2, +0.2] | -16.6 s [-23.8, -9.4] |
| 10 | 300 | greedy | 90.8% | 114.8 | 182.4 | 285.0 | 342.4 | 49.3% | baseline |  | baseline |
| 10 | 300 | insertion | 87.8% | 79.2 | 146.9 | 243.2 | 303.0 | 46.2% | -35.6 s [-48.2, -23.0] |  | -39.4 s [-50.9, -27.8] |
| 10 | 300 | cpsat | 86.5% | 67.8 | 135.5 | 224.1 | 368.3 | 45.0% | -47.0 s [-63.6, -30.3] | -11.4 s [-15.6, -7.2] | +25.9 s [-9.9, +61.7] |
| 10 | 300 | insertion-fair | 88.0% | 82.0 | 149.7 | 232.0 | 284.5 | 46.4% | -32.8 s [-44.7, -20.9] |  | -57.9 s [-73.8, -41.9] |
| 10 | 300 | cpsat-fair | 87.6% | 77.1 | 144.7 | 215.8 | 242.4 | 45.8% | -37.7 s [-51.4, -24.0] | -5.0 s [-7.2, -2.7] | -100.0 s [-121.3, -78.7] |

CP-SAT solver statistics, over all decisions of all seeds:

| Robots | Orders/h | Policy | Decisions | Searched | Proven optimal | Stopped at limit | Fallback | Improved on insertion | Mean gap when stopped | Solve s mean / P95 / max |
|---:|---:|---|---:|---:|---:|---:|---:|---:|---:|---|
| 5 | 100 | cpsat | 2434 | 302 | 302 (100.0%) | 0 (0.0%) | 0 (0.0%) | 112 | - | 0.010 / 0.024 / 0.103 |
| 5 | 100 | cpsat-fair | 2422 | 304 | 304 (100.0%) | 0 (0.0%) | 0 (0.0%) | 130 | - | 0.010 / 0.022 / 0.065 |
| 5 | 150 | cpsat | 3367 | 1856 | 1579 (85.1%) | 277 (14.9%) | 0 (0.0%) | 1113 | 36.4% | 0.088 / 0.472 / 0.667 |
| 5 | 150 | cpsat-fair | 3336 | 1993 | 1977 (99.2%) | 16 (0.8%) | 0 (0.0%) | 1270 | 19.3% | 0.034 / 0.095 / 0.534 |
| 10 | 200 | cpsat | 5213 | 485 | 485 (100.0%) | 0 (0.0%) | 0 (0.0%) | 97 | - | 0.013 / 0.026 / 0.052 |
| 10 | 200 | cpsat-fair | 5215 | 503 | 503 (100.0%) | 0 (0.0%) | 0 (0.0%) | 162 | - | 0.015 / 0.026 / 0.051 |
| 10 | 300 | cpsat | 7687 | 3915 | 3317 (84.7%) | 598 (15.3%) | 0 (0.0%) | 1967 | 22.3% | 0.095 / 0.460 / 0.617 |
| 10 | 300 | cpsat-fair | 7474 | 4037 | 4031 (99.9%) | 6 (0.1%) | 0 (0.0%) | 2717 | 0.0% | 0.041 / 0.107 / 0.457 |

400 runs in 134.3 s.
Every order was delivered in every run.
```

### Reading the results

- **The three M3.4 variants reproduce exactly**: the same KPIs and the same solver outcomes, decision
  for decision. Only the solve times differ (see the last point).
- **Worst first fixes the worst case.** Against greedy, `cpsat-fair` cuts the worst-case completion
  time in every configuration, with intervals that exclude zero:
  - −80.3 s [−102.0, −58.7] at 5 robots and 150 orders/h (334.6 s against 414.9 s);
  - −100.0 s [−121.3, −78.7] at 10 robots and 300 orders/h (242.4 s against 342.4 s);
  - −13.7 s and −16.6 s at moderate load.

  Plain `cpsat`, by contrast, is +210.1 s [+100.4, +319.7] at 5 robots and 150 orders/h.
- **It also has the best P95 of all five variants at high load:** 304.2 s and 215.8 s, against 357.4 s
  and 285.0 s for greedy.
- **The price is part of the mean gain.** At high load, `cpsat-fair` cuts mean completion time by
  31.6 s and 37.7 s against greedy, where plain `cpsat` cuts it by 50.8 s and 47.0 s. The fair variant
  keeps 62% and 80% of the gain, and turns a worse worst case into a much better one.
- **CP-SAT still beats insertion under the same objective at high load:** −9.3 s [−17.1, −1.5] and −5.0 s
  [−7.2, −2.7]. At moderate load the difference is not resolved (+0.2 s and +0.0 s, intervals across
  zero).
- **The worst-first model is easier to solve.** 99.2% and 99.9% of searched decisions were proven
  optimal, against about 85% for plain `cpsat`. The longest lead time dominates the cost, and its lower
  bound is often tight, so the proof closes sooner.
- **A correction to the M3.4 reading.** Now that the worst case has a paired interval, plain CP-SAT's
  worse worst case is resolved only at 5 robots and 150 orders/h. At 10 robots and 300 orders/h it is
  +25.9 s [−9.9, +61.7]. The difference of the means reported in M3.4 (368.3 s against 342.4 s) is
  real, but at 20 seeds it cannot be told apart from zero.
- **Solve times are not reproducible; solver outcomes are.** The identical plain-`cpsat` solves took
  0.09 s on average in this run and 0.32 s in the M3.4 run, on the same laptop with the same 8
  processes. Machine state, such as power and thermal limits, changes wall time a lot. The
  deterministic limit keeps that from changing any plan.

### Recommendation

`cpsat-fair` is the best overall policy in this model:
- it has the best P95 and the best worst case at high load;
- it keeps most of the mean gain over greedy;
- it is the easiest to solve.

Plain `cpsat` remains the choice if only the mean matters. Insertion with worst first is the fallback
without a solver: it gets most of the way there, but its worst case at high load is clearly behind
(378.9 s and 284.5 s).

### What this does not show

The same limits as the M3.4 section apply: no congestion, availability predicted exactly, one map with
uniform demand, 20 seeds, and Windows only. In addition, the worst-first objective protects only the
single order that has waited longest at each decision. Its effect on the tail beyond the maximum and
the P95 is not measured.
