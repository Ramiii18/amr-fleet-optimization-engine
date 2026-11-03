# Benchmarks

Every result below is the unchanged output of a script in `scripts/`. Runtimes depend on the machine;
node-expansion counts do not.

## Routing: Dijkstra vs A* (M1, 2026-11-03)

**Command:** `.venv\Scripts\python.exe scripts\compare_routing.py` with the defaults: 300 queries per map,
5 repeats per query, seed 42.

**Machine:** MSI Thin GF63 12UCX, Intel Core i7-12650H, 32 GB RAM, Windows 11, on AC power. The CPU
model name comes from Windows; Python only reports the family and stepping string shown below.

**Queries:** random pairs of non-intersection locations (storage, stations, chargers, parking) on the
generated presets, identical for all algorithms. Runtime per query is the fastest of 5 runs; the
table shows the median and P95 of those values across queries.

```text
Routing comparison, 2026-11-03
Environment: Python 3.13.1, Windows-11-10.0.26200-SP0, CPU Intel64 Family 6 Model 154 Stepping 3, GenuineIntel, amr-fleet-optimization-engine 0.0.1 (commit cef1abf)
Settings: 300 queries per map, 5 repeats per query, seed 42

| Map | Nodes | Edges | Algorithm | Mean expanded | P95 expanded | Median µs | P95 µs |
|---|---:|---:|---|---:|---:|---:|---:|
| small | 45 | 74 | Dijkstra | 26.8 | 44 | 14.6 | 22.1 |
| small | 45 | 74 | A* Euclidean | 17.6 | 33 | 13.4 | 24.8 |
| small | 45 | 74 | A* Manhattan | 15.9 | 32 | 11.8 | 22.9 |
| medium | 179 | 296 | Dijkstra | 94.1 | 172 | 53.0 | 94.5 |
| medium | 179 | 296 | A* Euclidean | 43.0 | 113 | 28.6 | 89.0 |
| medium | 179 | 296 | A* Manhattan | 33.3 | 88 | 21.5 | 71.3 |
| large | 461 | 736 | Dijkstra | 241.1 | 433 | 143.2 | 250.1 |
| large | 461 | 736 | A* Euclidean | 101.3 | 257 | 66.2 | 207.9 |
| large | 461 | 736 | A* Manhattan | 71.7 | 199 | 38.2 | 166.3 |

All algorithms returned identical route lengths for every query.
```

### Reading the results

- **Correctness first.** All three algorithms returned the same route length for all 900 queries.
- **Expansions.** On the large map, A* with Manhattan distance settles 3.4× fewer nodes than Dijkstra on
  average (71.7 vs 241.1), and A* with Euclidean distance 2.4× fewer. Manhattan is the tighter estimate
  because every edge in these maps is axis-aligned: no route can be shorter than |dx| + |dy|.
- **Runtime follows expansions, but less than proportionally.** Each A* expansion costs more than a
  Dijkstra one: a heuristic call for every queued neighbour, and a wider heap entry. On the large map,
  A* Manhattan is 3.7× faster at the median (38.2 vs 143.2 µs).
- **On the small map the gain almost disappears.** A* Euclidean's P95 is even slightly worse than
  Dijkstra's (24.8 vs 22.1 µs). With 45 nodes the search is so short that the heuristic's overhead weighs
  as much as the expansions it saves.
- **The worst cases (P95) improve less than the typical case (median).** The heuristic doesn't know which
  way the aisles run. When a target is against an aisle's direction, the route has to detour through a
  cross aisle, and A* ends up exploring much of what Dijkstra explores.
- **Noise.** Two consecutive runs of the same code differed by up to about 8% in runtime (large map,
  Dijkstra median: 135.1 vs 143.2 µs). Expansion counts were identical.

### What this does not show

- This is single-robot, static routing: no other robots, no time dimension, no congestion. It is a
  building block, not a fleet-level result.
- The microsecond figures are for pure Python on one laptop, so they are mainly meaningful relative to
  each other. Routing cost inside the dispatcher (M2), and space-time search, which makes the search
  space much larger (M4), will be measured separately.
