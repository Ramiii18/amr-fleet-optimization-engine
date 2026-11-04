# Roadmap

The plan assumes about 10 h/week. Week 1 started on 2026-11-03. Since then the working method has been
build-first: Claude implements and the docs explain each step. The roadmap is re-planned at every
milestone, and correctness comes before scope: if time runs short, features move later rather than
quality dropping.

| Milestone | Content | Status |
|---|---|---|
| **M1** Routing library | Warehouse graph, Dijkstra, A*, map generator, JSON maps, tests, comparison | **Done** (2026-11-03) |
| **M2** MVP v0.1 | Robot/order domain model, discrete-event simulator, greedy and Hungarian dispatch, KPIs, scenario generator, GitHub + CI | In progress |
| **M3** Optimization | CP-SAT bounded-horizon dispatch, paired-seed benchmark harness | Not started |
| **M4** Traffic v0.2 | Space-time A*, reservation table, prioritized planning, conflict tests, mutex zones, deadlock detection | Not started |
| **M5** Portfolio v1 | Benchmarks at 3–5, 10 and 25 robots, documentation, visualization, honest limitations | Not started |

**MVP definition (M2):** the engine takes a synthetic warehouse map and a seeded stream of transport orders
and dispatches the orders to N identical robots. The simulator executes them with correct integer travel
times, checks invariants, and reports KPIs for at least two dispatch policies on identical scenarios.
Collisions are not modelled at this stage. That limitation is documented, and the KPI difference between
MVP results and traffic-aware results later becomes a measured cost of congestion.

## M1: what was delivered

- [x] Environment and project skeleton (venv, pyproject, ruff, mypy strict, pytest)
- [x] `Node`, `Edge` and `WarehouseGraph` with construction-time validation
- [x] Dijkstra, point-to-point and one-to-all, with blocked edges
- [x] `validate_route`, and oracle tests against NetworkX on 60 random graphs
- [x] A* with zero, Euclidean and Manhattan heuristics
- [x] Deterministic grid-warehouse generator, 3 presets, strict JSON map format, committed example maps
- [x] Dijkstra vs A* comparison script; results in [benchmarks.md](benchmarks.md)
- [x] Algorithm explanations in [algorithms.md](algorithms.md)
- [x] GitHub repository and CI workflow: moved to M2 because it needed the owner's GitHub account, done
  there on 2026-11-04

## M1 acceptance criteria and results

1. **Reproducible environment:** met. The environment was set up from the README; the owner created the
   `.venv` and ran the checks.
2. **ruff check, ruff format and mypy (strict) report no errors:** met at every commit.
3. **Hand-computed edge cases pass** (source equals target, cheaper branch, one-way detour, unreachable,
   unknown node, ties, blocked edges): met.
4. **Dijkstra matches NetworkX on at least 50 seeded graphs, including unreachable targets:** met, with
   60 seeds, with and without blocked edges.
5. **A* matches Dijkstra on at least 50 seeded geometric graphs:** met, with 60 seeds, with and without
   blocked edges.
6. **Every returned route passes `validate_route`:** met.
7. **A map with at least 300 nodes is deterministic, valid and strongly connected:** met. The large preset
   has 461 nodes.
8. **Comparison output recorded exactly as measured:** met, in [benchmarks.md](benchmarks.md).
9. **Every algorithm explained (problem, approach, complexity, limitations):** met, in
   [algorithms.md](algorithms.md). This replaces the original exercise-based understanding check after the
   switch to build-first.
10. **Small, meaningful commits:** met, one commit per increment.

## M2: planned increments (preliminary)

1. **Time model.** Integer ticks, robot speed, edge travel times computed with `ceil` (D6).
2. **Domain model.** Robot and transport order, and a robot state machine with explicitly listed valid
   transitions.
3. **Scenario generator.** A seeded order stream: arrival process, pickup at storage, drop-off at a
   station.
4. **Discrete-event simulation kernel.** A deterministic event queue, a plan executor, invariant checks
   and an event log.
5. **Dispatch policies.**
   - Greedy: nearest idle robot.
   - Hungarian: optimal assignment for each decision round, using `scipy`.
6. **KPIs, CLI runner and comparison.** Throughput, waiting time, completion time (mean and P95),
   distance and utilization, compared on identical seeds.
7. **GitHub repository and CI.** Done 2026-11-04: public repository under the MIT license. The first CI
   run (commit `1b465b3`) passed on both Windows and Linux (D11).

## Phase 2 backlog (after M5, or earlier if time allows)

- Battery model, charging policy, charger queueing
- Robot faults, blocked corridors, event-driven replanning
- FastAPI service with background jobs for CPU-bound solving; Docker (requires WSL2)
- Local search / Large Neighbourhood Search for dispatch
- Animated visualization
- A mock adapter shaped like VDA 5050 messages, validated against the published specification
