# Roadmap

The plan assumes about 10 h/week. Week 1 started on 2026-11-03. Since then the working method has been
build-first: Claude implements and the docs explain each step. The roadmap is re-planned at every
milestone, and correctness comes before scope: if time runs short, features move later rather than
quality dropping.

| Milestone | Content | Status |
|---|---|---|
| **M1** Routing library | Warehouse graph, Dijkstra, A*, map generator, JSON maps, tests, comparison | **Done** (2026-11-03) |
| **M2** MVP v0.1 | Robot/order domain model, discrete-event simulator, greedy and Hungarian dispatch, KPIs, scenario generator, GitHub + CI | **Done** (2026-11-04) |
| **M3** Optimization | Look-ahead and multi-order sequencing over a bounded horizon: insertion heuristic and CP-SAT, compared in simulation | In progress (re-planned 2026-11-04) |
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

## M2: what was delivered

1. **Time model.** Done 2026-11-04: integer ticks, robot speed, edge travel times rounded up per edge
   (D6, D12).
2. **Domain model.** Done 2026-11-04: transport order, immutable robot state, and a robot state machine
   with explicitly listed valid transitions (D13).
3. **Scenario generator.** Done 2026-11-04: seeded order stream (Bernoulli arrivals per tick), pickup at
   storage, drop-off at a station, shift-start fleet placement (D14).
4. **Discrete-event simulation kernel.** Done 2026-11-04: deterministic event queue, executor with
   validated decisions, invariant checks after every tick, and an event log (D15).
5. **Dispatch policies.** Done 2026-11-04 (D16):
   - Greedy: oldest order first, nearest idle robot.
   - Hungarian: optimal assignment of idle robots to the oldest orders, using `scipy`.
   - Optional decision rounds in the simulator, so that optimal matching has room to help.
6. **KPIs, CLI runner and comparison.** Done 2026-11-04: KPIs from the event log (D17), the `amr-sim`
   command, and `scripts/compare_dispatch.py` with paired seeds; results in [benchmarks.md](benchmarks.md).
7. **GitHub repository and CI.** Done 2026-11-04: public repository under the MIT license. The first CI
   run (commit `1b465b3`) passed on both Windows and Linux (D11).

## M2 acceptance against the MVP definition

- **Dispatches a seeded order stream to N identical robots on a synthetic map:** met (`scenario/`,
  `dispatch/`).
- **Correct integer travel times:** met. Exact phase durations are checked after every tick, and on
  generated runs they are checked against times computed independently from Dijkstra.
- **Invariants checked:** met. The state machine checks every transition, and the simulator checks its
  invariants after every tick; the checks are tested by breaking them on purpose.
- **KPIs for at least two policies on identical scenarios:** met. Greedy and Hungarian, each at every
  event and in rounds, on 20 paired seeds.
- **Collisions not modelled, and documented:** met (README, ARCHITECTURE, algorithms section 8).

**Finding that shapes M3:** optimal per-decision matching gave no measurable gain over greedy, and
batching made both worse. M3 (CP-SAT) should therefore target what greedy cannot do: look-ahead to
robots about to finish, sequencing several orders per robot, and time windows. It should not re-solve
the same one-sided decision.

## M3: plan (re-planned 2026-11-04)

**Why the re-plan.** The original M3 item, "CP-SAT bounded-horizon dispatch", stays. After the M2
finding, its model has to do what greedy cannot (D18):
- **Look-ahead:** busy robots are planned too, from the time and place where they will finish their
  current order. An order may wait for a busy robot that will be closer soon.
- **Sequencing:** each robot gets a sequence of orders, so a robot that frees up can take a nearby order
  instead of the oldest one.
- **Receding horizon:** each decision plans the oldest K pending orders, commits only the first job of
  each idle robot, and plans again at the next decision.

**Increments:**
1. **Planning model.** Done 2026-11-04: predicted robot availability, the planning problem built from a
   dispatch snapshot, a plan evaluator, and a brute-force oracle for the tests.
2. **Insertion heuristic and receding-horizon policy.** Done 2026-11-04: cheapest insertion builds the
   plan, and the policy commits the first jobs (D19). This is the heuristic that CP-SAT has to beat.
3. **CP-SAT model.** Done 2026-11-04: one giant-tour circuit (measured faster than one circuit per
   robot), the insertion plan as a hint and as a fallback, and deterministic solver limits, checked
   against the brute-force oracle. Adds OR-Tools as a runtime dependency (D20).
4. **Comparison.** Done 2026-11-04: greedy vs insertion vs CP-SAT on paired seeds, with solver
   statistics (D21); results in [benchmarks.md](benchmarks.md). At high load, CP-SAT cut mean completion
   time by 24–26% against greedy and by about 11 s against insertion, but its worst-case completion
   time grew (625 s against 415 s for greedy at 5 robots and 150 orders/h).
5. **Due times, only if increment 4 leaves the question open.** Triggered by the worst-case result of
   increment 4: look-ahead and sequencing starve some distant orders. Next: add per-order due times and
   a lateness term.

**Acceptance criteria:**
- CP-SAT finds the brute-force optimum on at least 200 random small instances, and the insertion
  heuristic never beats it.
- Both new policies deliver every order of generated scenarios, and runs are reproducible: the same seed
  gives an identical event log, CP-SAT included.
- The comparison reports paired differences with 95% confidence intervals. Every solver timeout and
  fallback is counted, not hidden.
- CP-SAT earns its place only if it beats the insertion heuristic with a confidence interval that
  excludes zero. If it doesn't, the result says so, and the heuristic is the recommendation.

## Phase 2 backlog (after M5, or earlier if time allows)

- Battery model, charging policy, charger queueing
- Robot faults, blocked corridors, event-driven replanning
- FastAPI service with background jobs for CPU-bound solving; Docker (requires WSL2)
- Local search / Large Neighbourhood Search for dispatch
- Animated visualization
- A mock adapter shaped like VDA 5050 messages, validated against the published specification
