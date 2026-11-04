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
