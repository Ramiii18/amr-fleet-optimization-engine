# Roadmap

The plan assumes about 10 h/week, which is roughly four 2.5-hour sessions. Week 1 started on 2026-11-03.
The roadmap is re-planned at every milestone. Correctness and understanding come before scope: if time runs
short, features move later rather than quality dropping.

| Weeks | Milestone | Content | Status |
|---|---|---|---|
| 1–2 | **M1** Routing library | Project setup, warehouse graph, Dijkstra, A*, map generator, tests | In progress |
| 3–5 | **M2** MVP v0.1 | Robot/order domain model, discrete-event simulator, greedy and Hungarian dispatch, KPIs, scenario generator, GitHub + CI | Not started |
| 6–7 | **M3** Optimization | CP-SAT bounded-horizon dispatch, paired-seed benchmark harness | Not started |
| 8–10 | **M4** Traffic v0.2 | Space-time A*, reservation table, prioritized planning, conflict tests, mutex zones, deadlock detection | Not started |
| 11–12 | **M5** Portfolio v1 | Benchmarks at 3–5, 10 and 25 robots, documentation, visualization, honest limitations | Not started |

**MVP definition (M2):** the engine takes a synthetic warehouse map and a seeded stream of transport orders
and dispatches the orders to N identical robots. The simulator executes them with correct integer travel
times, checks invariants, and reports KPIs for at least two dispatch policies on identical scenarios.
Collisions are not modelled at this stage. That limitation is documented, and the KPI difference between
MVP results and traffic-aware results later becomes a measured cost of congestion.

## M1 sessions

- [ ] S1: Environment and project skeleton
- [ ] S2: Python for structured code (dataclasses, Enum, type hints), `Node` and `Edge`
- [ ] S3: `WarehouseGraph` with validation (test-first)
- [ ] S4: Dijkstra (implemented by the owner, stepped through in the debugger)
- [ ] S5: Oracle tests against NetworkX, `validate_route`, one-to-all distances
- [ ] S6: A* and heuristics
- [ ] S7: Seeded warehouse generator and JSON map files
- [ ] S8: Blocked edges, Dijkstra vs A* comparison script, milestone review
- [ ] Stretch: GitHub repository and CI workflow (otherwise the first task of week 3)

## M1 acceptance criteria

1. A fresh `.venv` built from the README instructions passes `pytest`.
2. ruff check, ruff format and mypy (strict) report no errors.
3. All hand-computed edge-case tests pass: source equals target, cheaper branch chosen, one-way detour,
   unreachable target returns `None`, unknown node raises, ties resolve deterministically, blocked edges are
   respected.
4. Dijkstra, both point-to-point and one-to-all, matches NetworkX on at least 50 seeded random graphs,
   including cases where the target is unreachable.
5. A* finds the same cost as Dijkstra on at least 50 seeded geometric graphs.
6. Every returned route passes `validate_route`.
7. The generated map with at least 300 nodes is deterministic, passes validation, and every station can
   reach every other station.
8. The comparison script's output is recorded exactly as measured.
9. Understanding check: the owner wrote the Dijkstra and A* cores, re-implemented Dijkstra in a timed
   exercise from a blank file, and gave written answers to three interview questions.
10. At least one meaningful commit per session.

## Phase 2 backlog (after week 12, or earlier if time allows)

- Battery model, charging policy, charger queueing
- Robot faults, blocked corridors, event-driven replanning
- FastAPI service with background jobs for CPU-bound solving; Docker (requires WSL2)
- Local search / Large Neighbourhood Search for dispatch
- Animated visualization
- A mock adapter shaped like VDA 5050 messages, validated against the published specification
