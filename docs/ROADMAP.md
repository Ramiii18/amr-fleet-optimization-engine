# Roadmap

The plan assumes about 10 h/week. Week 1 started on 2026-11-03. Since then the working method has been
build-first: Claude implements and the docs explain each step. The roadmap is re-planned at every
milestone, and correctness comes before scope: if time runs short, features move later rather than
quality dropping.

| Milestone | Content | Status |
|---|---|---|
| **M1** Routing library | Warehouse graph, Dijkstra, A*, map generator, JSON maps, tests, comparison | **Done** (2026-11-03) |
| **M2** MVP v0.1 | Robot/order domain model, discrete-event simulator, greedy and Hungarian dispatch, KPIs, scenario generator, GitHub + CI | **Done** (2026-11-04) |
| **M3** Optimization | Look-ahead and multi-order sequencing over a bounded horizon: insertion heuristic and CP-SAT, compared in simulation | **Done** (2026-11-05) |
| **M4** Traffic v0.2 | Reservation table, safe-interval path planning (SIPP), prioritized planning, conflict checking, holding spots, measured cost of congestion | **Done** (2026-11-05) |
| **M5** Portfolio v1 | Scaling benchmark with traffic at 3, 5, 10 and 25 robots, decision latency, figures and animation, portfolio README, consolidated limitations | In progress (planned 2026-11-05) |

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
   increment 4: look-ahead and sequencing starve some distant orders. Done 2026-11-05, with a
   parameter-free worst-first objective instead of due times (D22). It minimises the longest lead time
   first, then the total. `cpsat-fair` cut the worst case by 80–100 s against greedy at high load, and
   kept 62–80% of the gain in mean completion time.

## M3 acceptance

- **CP-SAT finds the brute-force optimum on at least 200 small instances, and insertion never beats
  it:** met. 250 random and 50 map-based problems with total completion time, and 150 random problems
  with worst first; insertion was never better than the optimum.
- **Both new policies deliver every order, and runs are reproducible, CP-SAT included:** met. Tests
  compare event logs of repeated runs. The M3.5 benchmark run reproduced the M3.4 KPIs and solver
  outcomes exactly.
- **Paired differences with 95% intervals, every timeout and fallback counted:** met. Each decision is
  reported as no search needed, proven optimal, stopped at the limit (with its gap), or fallback.
- **CP-SAT earns its place only if it beats insertion with an interval that excludes zero:**
  - with total completion time: met in 3 of 4 configurations;
  - with worst first: met in both high-load configurations, not resolved at moderate load.

  CP-SAT pays off where queues form. When idle robots are usually available, insertion is enough.

**Recommendation from M3:** `cpsat-fair`. It has the best P95 and worst case at high load, keeps most of
the mean gain, and is the easiest variant to solve ([benchmarks.md](benchmarks.md)).

**Finding that shapes M4:** all M3 gains come from planning on *exact* predictions of when and where
robots become free, and from less empty travel. With traffic, travel times and availability become
estimates, and congestion may eat part of the saving. M4 should re-run this comparison with traffic,
re-plan when a prediction turns out wrong, and report the cost of congestion separately.

**Acceptance criteria:**
- CP-SAT finds the brute-force optimum on at least 200 random small instances, and the insertion
  heuristic never beats it.
- Both new policies deliver every order of generated scenarios, and runs are reproducible: the same seed
  gives an identical event log, CP-SAT included.
- The comparison reports paired differences with 95% confidence intervals. Every solver timeout and
  fallback is counted, not hidden.
- CP-SAT earns its place only if it beats the insertion heuristic with a confidence interval that
  excludes zero. If it doesn't, the result says so, and the heuristic is the recommendation.

## M4: plan (2026-11-05)

**Goal.** Robots stop passing through each other. Every robot's movement becomes a timed path that
reserves nodes and corridors, so no two robots are ever in the same place at the same time. The
dispatch policies are then compared again, with and without traffic, to measure the cost of congestion
(the M3 finding).

**Model (D23, D24).**
- **Resources.** A node holds at most one robot at a time. So does a corridor (a pair of directed
  edges between two nodes), in either direction, which rules out head-on, swap and following
  conflicts on one corridor.
- **Prioritized planning.** When a robot gets a job, its whole itinerary is planned at once, against
  the reservations of all robots planned before it:
  - drive to the pickup and load;
  - drive to the drop-off and unload;
  - drive to a free holding spot nearby, and stay there.
- **Holding spots.** Robots stop only at endpoints: storage, stations, chargers and parking, which
  are dead-end spurs on every generated map. Stations are never used for parking. An idle robot that
  stands on a pickup another robot needs is moved to another free endpoint.
- **Search.** Safe-interval path planning (SIPP) finds the earliest itinerary, waiting where needed.
  It searches over intervals in which a node is free, so it doesn't step through every 0.1 s tick.

**Increments:**
1. **Reservations and conflict checking.** Done 2026-11-05: timed paths, a reservation table for nodes
   and corridors (including open-ended holds), and an independent checker that finds every conflict
   between paths.
2. **SIPP itinerary planner.** Done 2026-11-05: earliest-arrival timed paths through stops with service
   times, ending in a hold. They match a brute-force search over (node, tick) states on 300 random
   small instances.
3. **Traffic-aware simulator.** Done 2026-11-05: itineraries planned at assignment, holding spots,
   moves off needed endpoints, cycle breaking, and trajectories recorded in both modes. Every
   generated test run with traffic has zero conflicts by the independent checker.
4. **Comparison.** Done 2026-11-05: every policy with and without traffic, on paired seeds
   ([benchmarks.md](benchmarks.md)). The planning policies use the planned availability of busy
   robots.
5. **Stretch, else Phase 2.** Mutex zones (groups of nodes and corridors with capacity 1), and random
   delays with an execution policy that keeps the planned order of robots at each resource. **Moved to
   Phase 2** (see the backlog): the core increments answered M4's question, and these two are
   extensions of it.

**Acceptance criteria:**
- SIPP's arrival time equals the brute-force optimum on at least 200 small random instances, and its
  paths never conflict with the existing reservations.
- Head-on, swap, intersection and station-contention cases are tested by hand.
- With traffic, every generated run delivers every order, is reproducible, and has zero conflicts in
  its executed trajectories, as found by the independent checker.
- The comparison reports each policy's cost of congestion with paired confidence intervals. A planning
  failure, which the design rules out, would stop the run with an error rather than be hidden.

## M4 acceptance

- **SIPP equals the brute-force optimum on at least 200 small instances, and never conflicts with the
  reservations:** met. 300 random grids: 288 possible itineraries with equal arrival times, and 12
  impossible ones on which both agree.
- **Head-on, swap, intersection and station contention tested by hand:**
  - met for the conflict checker (head-on, swap, following, intersection crossing, holds);
  - met for the simulator with traffic: crossing robots, station contention, moving out of the way,
    and a cycle. Each matches a hand-derived timeline tick for tick, except the cycle, which is
    checked for its outcome.
- **With traffic, every run delivers every order, is reproducible and has zero conflicts:** met. 36
  generated test runs, and the 240 runs with traffic of the comparison.
- **Cost of congestion with paired intervals; planning failures stop the run:** met. Planning
  failures raise an error, and none occurred.
- **Stretch goals (mutex zones, delays):** not done, moved to Phase 2.

**Finding:**
- The cost of traffic is 5–12 s at moderate load, and 67–189 s at high load.
- Planning policies cut it by half or more at high load. At 10 robots and 300 orders/h, `cpsat-fair`
  beats greedy by 160 s with traffic, against 38 s in free flow.

**What shapes M5:**
- The portfolio benchmarks should use traffic mode, because free flow overstates performance and
  understates the value of planning.
- The 25-robot benchmark of the original plan will test whether prioritized planning and the station
  layout still cope.

## M5: plan (2026-11-05)

**Goal.** Present what exists, honestly and reproducibly:
- how the engine scales;
- how fast it decides;
- what its behaviour looks like;
- where its limits are.

Every number and every figure comes from a committed script.

**Increments:**
1. **Scaling benchmark with traffic.** Done 2026-11-05 ([benchmarks.md](benchmarks.md)).
   - Fleets of 3, 5, 10 and 25 robots on the large map.
   - Two loads per fleet: 20 and 30 orders per robot per hour, roughly moderate and high
     utilization.
   - Greedy, `insertion-fair` and `cpsat-fair`, on 20 paired seeds.
   - It also measures **decision latency**: the wall time of each dispatch decision, the policy plus
     the traffic planning that executes it.
   - Per-run results are also written as CSV, for the figures.
2. **Visualization** (`viz/`, matplotlib). Done 2026-11-05:
   - the map with robot trajectories;
   - a chart of the scaling results, drawn from the CSV;
   - a short animation of a run with traffic.

   A figure script regenerates them all into `docs/figures/`.
3. **Portfolio documentation.**
   - The README becomes the landing page: what the engine does, headline results with figures, how to
     reproduce every number, and the architecture.
   - A consolidated `docs/LIMITATIONS.md`.
   - The project overview distinguishes what is built from what is Phase 2.

**Acceptance criteria:**
- The scaling benchmark is recorded verbatim. Every order is delivered and every run with traffic has
  zero conflicts. Decision latency is reported per policy and fleet size.
- Every figure is produced by a committed script from simulation output; no image is edited by hand.
- A reader can reproduce every reported number with the commands in the README.
- No document implies real deployment or control of physical robots, and the limitations are stated
  in one place.

## Phase 2 backlog (after M5, or earlier if time allows)

- **From M4:** mutex zones (groups of nodes and corridors with capacity 1); random delays with an
  execution policy that keeps the planned order of robots at each resource (an action dependency
  graph); traffic-aware cost estimates for the planners; a fleet-wide path planner (for example
  conflict-based search) instead of prioritized planning.

- Battery model, charging policy, charger queueing
- Robot faults, blocked corridors, event-driven replanning
- FastAPI service with background jobs for CPU-bound solving; Docker (requires WSL2)
- Local search / Large Neighbourhood Search for dispatch
- Animated visualization
- A mock adapter shaped like VDA 5050 messages, validated against the published specification
