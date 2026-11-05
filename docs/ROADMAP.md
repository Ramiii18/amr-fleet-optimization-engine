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
| **M5** Portfolio v1 | Scaling benchmark with traffic at 3, 5, 10 and 25 robots, decision latency, figures and animation, portfolio README, consolidated limitations | **Done** (2026-11-05) |

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
3. **Portfolio documentation.** Done 2026-11-05.
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

## M5 acceptance

- **Scaling benchmark recorded verbatim; every order delivered; zero conflicts; latency per policy and
  fleet size:** met. 480 runs (benchmarks.md, "Scaling with traffic").
- **Every figure produced by a committed script from simulation output, none edited by hand:** met.
  `scripts/make_figures.py` draws from `docs/results/scaling.csv` and one fixed run.
- **Every reported number reproducible with commands in the README:** met. The README maps each result
  to its script, its approximate run time and the section where it is recorded.
- **No implied deployment; limitations in one place:** met. The README, the project overview and
  [LIMITATIONS.md](LIMITATIONS.md) state the synthetic, non-deployed scope, and LIMITATIONS.md
  consolidates every known limit.

**Not done as specified:** the palette validator of the visualization guidance was not run, because it
needs Node.js, which is not part of the toolchain. The documented, pre-validated palette slots were
used unchanged (LIMITATIONS.md).

## Phase 2 plan (2026-11-05)

Phase 2 started on request after M5. Its milestones are ordered by what they add to the evidence and
by what they build on:

| Milestone | Content | Why in this place | Status |
|---|---|---|---|
| **P1** Delays and robust execution | Random delays while driving; executing plans in the planned order of robots at every node and corridor; naive execution as the baseline | It removes the main limitation of the M4 and M5 traffic results (execution is exact), and it builds the execution layer that P2 and P3 need | **Done** (2026-11-05) |
| **P2** Battery and charging | Battery model, chargers as shared resources, charging when low against opportunistic charging | The largest part of the original scope still missing | **Done** (2026-11-05) |
| **P3** Faults and replanning | Robot breakdowns and blocked corridors, replanning itineraries during execution | Builds on the P1 execution layer | In progress |
| **P4** Service layer | FastAPI service with background jobs for CPU-bound planning; Docker | Engineering breadth. Docker needs WSL2, a system install that is the owner's decision | Not started |

**P1 increments:**
1. **Executor.** Done 2026-11-05. Execute a set of conflict-free timed paths under random delays:
   - *naively*, keeping each robot's own schedule;
   - *in order*, where every robot uses every node and corridor in the planned order of robots.

   A robot that would reach an occupied node waits at the end of its corridor. Tested on hand cases
   and random plans: with no delays the plans are reproduced exactly; with delays, ordered execution
   has zero conflicts and always finishes.
2. **Delays in the fleet simulator.** Done 2026-11-05. Movement is executed edge by edge, phases end at
   actual arrivals, and new itineraries join the planned order of the robots already moving. Without
   delays, the M5 results reproduce exactly (checked on 72 runs).
3. **Comparison.** Done 2026-11-05 ([benchmarks.md](benchmarks.md)). Delay levels × execution
   policies × dispatch policies:
   - conflicts (expected under naive execution);
   - the cost of delays (under ordered execution).

**P1 acceptance criteria:**
- Ordered execution has zero conflicts and delivers every order at every delay level, on random plans
  and generated runs.
- With no delays, it reproduces the plans, and the M5 results, exactly.
- Naive execution's conflicts are measured and reported, not hidden.

## P1 acceptance

- **Ordered execution is conflict-free and complete at every delay level:** met.
  - 181 random fleets of the executor tests;
  - 24 generated simulator test runs;
  - 240 benchmark runs.

  Zero conflicts, every order delivered.
- **Without delays, the plans and the M5 results reproduce exactly:** met. The executor reproduces
  every random plan, 72 runs of the M5 scaling benchmark match the committed results KPI for KPI, and
  the benchmark rows without delays match M5.
- **Naive execution's conflicts measured and reported:** met. 136 conflicts in 71 of the random
  fleets, all 24 test runs, and 149 to 531 per run in the benchmark.

**Finding:**
- Robust execution keeps the fleet safe, but delays cost capacity, steeply at high load.
- With 20% of edges delayed, `cpsat-fair` beats greedy by 412 s at moderate load and 984 s at high
  load.

**What shapes P3:**
- Robots that fall far behind pass their delay on to everyone planned after them.
- Replanning or reordering in that case is the natural next step, and the same machinery handles
  breakdowns and blocked corridors.

## P2: plan (2026-11-05)

**Goal.** Robots run on batteries and share a few chargers. Charging takes robots out of service, and
how it is scheduled changes what the fleet delivers. The battery parameters are synthetic, chosen so
that charging matters within a one-hour run (D26).

**Increments:**
1. **Energy model.** Done 2026-11-05.
   - Integer energy units.
   - Each edge driven drains energy in proportion to its length.
   - A robot standing idle at a charger charges at a fixed rate.
   - Start levels are seeded.
2. **Charging in the simulator, when low.** Done 2026-11-05.
   - A robot that becomes idle below a low threshold drives to the nearest free charger and charges
     there.
   - It is hidden from dispatch until it reaches a high threshold, and dispatch sees when it will be
     ready.
   - Chargers are endpoints, reserved for charging. An idle, charged robot on a charger that another
     robot needs is moved away.
   - Invariant: no battery ever goes below zero.
3. **Opportunistic charging.** Done 2026-11-05.
   - Idle robots also top up when no order is waiting and a charger is free. They stay in service
     while they charge.
   - Changed while building (D27): the first version also returned low robots to service at the low
     threshold. It thrashed at high load in a probe, so it is kept as a third policy,
     `opportunistic-early`, and `opportunistic` keeps the D26 hysteresis (back in service at the high
     threshold).
   - Charging KPIs (`ChargingKpis`) and `amr-sim --charging`.
4. **Comparison.** Done 2026-11-05 ([benchmarks.md](benchmarks.md)).
   - The three charging policies, and runs without batteries, are compared under greedy and
     `cpsat-fair`, with charging KPIs: charging trips and top-ups, time out of service, time waiting
     for a charger, charger occupancy, and the lowest battery level.

**Acceptance criteria:**
- No battery ever goes below zero, in any test or benchmark run; checked by an invariant.
- Every order is delivered and every run with traffic is conflict-free, with charging.
- Without a battery model, all earlier results reproduce exactly.
- The comparison reports paired differences, and charging KPIs, for every charging policy.

## P2 acceptance

- **No battery ever goes below zero:** met. The simulator checks every battery after every edge.
  - The check never fired: not in the hand cases, the 108 generated test runs under three charging
    policies, or the 240 benchmark runs with batteries.
  - The lowest level in the benchmark was 18.2%. If every drive were shortest, the threshold
    argument would give at least 12.3% on that map.
  - The argument is not a proof under traffic, because of detours and moves out of the way
    (algorithms section 13.6). The invariant is what guarantees this criterion.
- **Every order is delivered and every run is conflict-free, with charging:** met. The 36 generated
  test configurations (12 scenarios, three charging policies) and all 320 benchmark runs have zero
  conflicts.
- **Without a battery model, earlier results reproduce exactly:** met.
  - 72 runs of the M5 scaling benchmark match the committed results KPI for KPI.
  - The end-of-run rule fired in none of 144 runs checked.
  - The benchmark rows without batteries match P1.
- **Paired differences and charging KPIs for every charging policy:** met.

**Findings:**
- Batteries cost capacity: 15% to 20% of robot time out of service under `when-low`. At high load
  the fleet is overloaded.
- Topping up idle robots (`opportunistic`) cuts mean completion by 26 s and the worst case by 70 to
  75 s at moderate load, and makes no detectable difference at high load.
- Returning low robots to work at the low threshold (`opportunistic-early`) is best at moderate
  load, but at high load robots thrash: about 200 charging trips per run instead of 11 to 13, and a
  worst case 520 to 600 s worse. The mean completion time does not show it.

**What shapes P3:**
- A breakdown takes a robot out of service like a low battery, but without warning. The
  out-of-service machinery can be reused: hidden from dispatch, with an expected return.
- A blocked corridor needs what P1 and P2 avoided: replanning itineraries that are already being
  executed.

## P3: plan (2026-11-05)

**Goal.** Robots break down while driving, and the fleet replans around them. Breakdowns are
*announced*: when a robot breaks down, the fleet manager learns it, and how long the repair will
take, at once. Delays (P1) are not announced.

**The problem replanning has to solve.** Ordered execution is deadlock-free because every plan
was reserved against every other in one clock, planned time, and every queue follows that clock
(D25). Robots that run late make planned times stale. A robot replanned in real time against stale
plans can be queued ahead of a robot that is physically ahead of it on one resource and behind it on
another, and the two then wait for each other forever. Replanning therefore re-times first (D28):
1. Compute the earliest schedule of every remaining plan from the current state, in the planned
   order. This is the one-pass longest-path computation of `execute_plans`, started mid-run.
2. Rebuild the reservation table and the queues from that schedule. All plans are again in one
   clock, and that clock matches reality as of now.
3. Replan the delayed robots with SIPP against it.

**Three recovery policies**, so that the comparison separates the effects:
- `wait`: nothing changes; robots behind a broken one wait (ordered execution, as in P1).
- `retime`: re-time at every breakdown, so that new jobs are planned knowing about it.
- `replan`: re-time, then replan every delayed robot; a new plan is kept only if it reaches its
  last stop earlier.

**Increments:**
1. **Re-timing.** Done 2026-11-05. `retime` in `traffic/execution.py`: the earliest schedule of
   the remaining plans from a state reached mid-run. Oracle: cutting an ordered execution at any tick and
   re-timing the rest reproduces it.
2. **Breakdowns in the simulator.** Done 2026-11-05. A seeded breakdown model (a share of edge traversals, a
   repair time range); `wait` and `retime`. With every job assigned at the start, `retime` must
   reproduce `wait` exactly: re-timing alone moves no robot.
3. **Replanning.** `replan`: robots in a corridor are committed to its end, so each delayed robot
   replans from the next node it will stand on, through its remaining stops. Hand cases (a robot
   detours around a broken one and arrives earlier than by waiting) and generated runs (every
   order delivered, zero conflicts, reproducible).
4. **Comparison.** Breakdown levels × recovery policies × greedy and `cpsat-fair`.

Blocked corridors without a robot (a closed aisle) reuse the same machinery and follow if the
breakdown results justify it; otherwise they go to the backlog.

**Acceptance criteria:**
- Every order is delivered and every run is conflict-free, under every recovery policy.
- Re-timing reproduces ordered execution (oracle), and moves no robot by itself.
- Without breakdowns, all earlier results reproduce exactly.
- The comparison reports the cost of breakdowns and the paired effect of each recovery policy.

## Phase 2 backlog (unordered)

- **From P2:** a CP-SAT schedule of charging slots over a horizon (intervals with charger capacity),
  against the rule-based policies.
- **From P2:** make the battery safety argument a proof under traffic. Check each planned itinerary's
  energy against the robot's level, and move low robots only towards a charger.

- **From M4:** mutex zones (groups of nodes and corridors with capacity 1); traffic-aware cost
  estimates for the planners; a fleet-wide path planner (for example conflict-based search) instead
  of prioritized planning.
- Local search / Large Neighbourhood Search for dispatch.
- A mock adapter shaped like VDA 5050 messages, validated against the published specification.
- Done in M5: animated visualization.
