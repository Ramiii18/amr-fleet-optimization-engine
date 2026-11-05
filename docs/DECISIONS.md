# Design decisions

A lightweight decision log (ADR-style). Each entry records what was decided, why, and what it costs.
Possible statuses: **Accepted**, **Proposed**, **Superseded by Dn**.

---

## D1: Python 3.13, venv + pip, one `pyproject.toml`
**Status:** Accepted (2026-11-03)
**Why:** Python 3.13 is installed and current, and OR-Tools publishes Windows wheels for it: versions 9.12–9.15
were checked with `pip index versions ortools`. Plain venv and pip teach the mechanics that tools like uv
or Poetry automate.
**Consequences:** There is no lock file yet, so benchmark runs must record the exact package versions they
used. Switching to uv later is a small change.

## D2: Our own graph implementation; NetworkX only as a test oracle
**Status:** Accepted (2026-11-03)
**Why:** Implementing the graph and the search algorithms is the skill this project exists to demonstrate.
Space-time search later also needs tight control over the inner loop. NetworkX is mature and independent
of our code, which makes it a good reference to test against.
**Consequences:** Our implementation needs thorough tests. `src/` never imports networkx.

## D3: Frozen dataclasses in the core; Pydantic only at boundaries
**Status:** Accepted (2026-11-03)
**Why:** Immutable objects stop algorithms from accidentally mutating shared state. Dataclasses are
standard-library and fast. Validating external input (files, API requests) is a boundary concern.
**Consequences:** Domain invariants are enforced in constructors (for example, `WarehouseGraph`
validation), not by a serialization library.

## D4: Expected outcomes are return values; invalid input raises an exception
**Status:** Accepted (2026-11-03)
**Why:** An unreachable target, such as one behind a blocked aisle, is a normal operational situation that
the caller must handle, so it returns `route=None`. An unknown node id is an input or programming error,
so it raises an exception.
**Consequences:** Callers must check `result.route is None` explicitly, and mypy forces them to.

## D5: Edge length must be > 0 and ≥ the straight-line distance between its endpoints
**Status:** Accepted (2026-11-03)
**Why:** A positive length rules out zero-duration moves, where a robot would "teleport". A length at least
equal to the straight-line distance guarantees that the Euclidean A* heuristic never overestimates. That
property is called admissibility, and it is what makes A* return optimal routes.
**Consequences:** Map validation rejects edges that break either rule, so the generator must produce
coordinates consistent with edge lengths.

## D6: Simulation and reservation time are integer ticks
**Status:** Accepted (2026-11-03). Implemented on 2026-11-04 in `domain/timing.py`; rounding details in D12.
**Why:** Reservation tables and conflict checks compare times for equality and overlap, and floats make
that fragile (`0.1 + 0.2 != 0.3`). Space-time search needs discrete time anyway.
**Consequences:** Edge durations are rounded up with `ceil(length / speed / tick)`, which errs on the
safe side. The tick length is a documented configuration value.

## D7: Determinism through injected random generators and deterministic tie-breaking
**Status:** Accepted (2026-11-03)
**Why:** Benchmarks and bug reports must be reproducible. Global random state is hidden shared state.
**Consequences:** Every function that needs randomness takes a `random.Random`. Priority queues break ties
with an insertion counter.

## D8: Directed edges only
**Status:** Accepted (2026-11-03)
**Why:** One-way aisles are common in warehouses, and a two-way corridor is simply two directed edges.
Algorithms stay simple and uniform.
**Consequences:** Detecting head-on conflicts on a single-lane two-way corridor requires knowing that two
directed edges share one physical corridor. That shared resource is added in the traffic phase (M4).

## D9: The warehouse generator is deterministic, with no random seed
**Status:** Accepted (2026-11-03). The original plan proposed a seeded generator.
**Why:** Nothing in the layout needs randomness. A map that is a pure function of its spec is easier to
reason about, diff and reproduce. Experiment variability comes from seeded order and event streams (M2+),
not from the map.
**Consequences:** The example maps in `scenarios/maps/` are regenerated with `scripts/generate_maps.py`,
and a test fails if a committed map no longer matches the generator. If randomized layouts are needed
later, they will take an injected `random.Random` (D7).

## D10: A* uses a closed set and never reopens nodes, so it requires a consistent heuristic
**Status:** Accepted (2026-11-03)
**Why:** With a consistent heuristic, the first expansion of a node is final. That gives a simpler and
faster search with the same optimality guarantee. Euclidean distance is consistent on every valid map
(D5 plus the triangle inequality). Manhattan distance is consistent on the axis-aligned generated maps.
**Consequences:** A heuristic that is admissible but not consistent can produce suboptimal routes. A test
documents this with an inflated heuristic. If such heuristics are ever needed, add node reopening and
benchmark it.

## D11: CI runs the four local checks on both Windows and Linux
**Status:** Accepted (2026-11-04)
**Why:** Development happens on Windows, while Docker and any deployment will run on Linux. Running both
catches platform differences early. M1 already hit one: map files were written with CRLF line endings on
Windows.
**Consequences:** Every run starts two jobs. They are free on public repositories; on private ones,
Windows jobs consume the monthly minutes faster. CI uses exactly the same commands as the README, so a
local pass predicts a CI pass.

## D12: Travel times are rounded up per edge, ignoring floating-point noise; routing stays in metres
**Status:** Accepted (2026-11-04)
**Why:**
- Each edge is rounded separately because a robot must reach every node at a whole tick, which
  reservation tables (M4) need.
- Rounding up means a plan never assumes a robot arrives earlier than it physically could.
- Values within a relative 1e-9 of a whole number snap to it, so floating-point noise doesn't cost an
  extra tick: 5.4 m at 1.2 m/s with 0.1 s ticks evaluates to 45.00000000000001 ticks, and is charged 45.
- Every edge takes at least one tick, so no move is instantaneous.

**Consequences:**
- A route can take up to one tick per edge longer than its exact time.
- When some edge needs rounding, the shortest route in metres may not be the fastest in ticks; a test
  shows this. Routing keeps minimising metres because the generated maps are exact at the default
  1 m/s and 0.1 s tick, and `TravelTimes.is_exact` reports this per scenario.
- If a scenario that is not exact matters later, add a time-weighted search: the routing functions would
  take an edge-weight function, with travel ticks as the weight.

## D13: Robot state is an immutable value with an explicit transition table; order progress is read from the robot
**Status:** Accepted (2026-11-04)
**Why:**
- A table of allowed phase changes is easy to review and to test exhaustively; every phase and
  transition combination is tested.
- Immutable states can be handed to planners as snapshots, and kept in an event log, without copying.
- Each non-idle phase stores its end tick, so a transition at the wrong tick raises an exception
  instead of silently skewing KPIs.
- An order's progress is the phase of the robot carrying it. Storing it separately would create a
  second state machine that must stay in sync with the first.

**Consequences:**
- No preemption or reassignment in the MVP: once assigned, an order stays with its robot until it is
  delivered. Reassignment after faults (Phase 2) needs new transitions in the table.
- An idle robot stays where it finished, at a drop-off. Without traffic (M2) this is harmless. With
  traffic (M4), a robot waiting at a station blocks it for others, so a parking policy will be needed.
- Orders that no robot carries yet, released but unassigned, and orders already delivered are tracked by
  the simulator (next increments).

## D14: Orders arrive as a per-tick Bernoulli process, drawn from named random streams
**Status:** Accepted (2026-11-04)
**Why:**
- Time is integer ticks (D6), so drawing "does an order arrive in this tick?" gives whole-tick release
  times with no rounding.
- Only `random()`, `choice()` and `sample()` are used. They need no platform maths library, unlike an
  exponential sampler, which calls `log`. A pinned scenario is checked by CI on Windows and Linux.
- Orders and robot placement use separate streams, seeded as `"<seed>:orders"` and `"<seed>:fleet"`. A
  different fleet size then faces exactly the same orders (common random numbers), which makes fleet-size
  comparisons paired.

**Consequences:**
- At most one order per tick. A rate that needs more is rejected rather than silently capped.
- Generation costs O(ticks), which is negligible in practice: about 0.8 ms per 30-minute stream.
- Python guarantees the `random()` sequence for a seed across versions, but not the algorithms behind
  `choice()` and `sample()`. The pinned test will flag a change after a Python upgrade, and the
  benchmarks record the Python version.

## D15: An event-driven simulator that calls the policy after each tick's events and validates its decisions
**Status:** Accepted (2026-11-04)
**Why:**
- Work grows with the number of events, not ticks: an hour at 0.1 s ticks is 36,000 ticks, but only a
  few events per order.
- Calling the dispatch policy after all of a tick's events gives it a complete, consistent snapshot:
  every robot that became idle and every order that arrived at that tick.
- Validating the whole decision before executing any of it means a bad decision is rejected cleanly,
  never half-applied.
- Checking invariants after every tick catches simulator bugs at the tick where they happen. The cost
  is O(robots) per tick, which is small next to routing and dispatch.

**Consequences:**
- The policy is called only when something changes. Fixed-interval decision rounds, for policies that
  benefit from batching, were added with D16.
- Same-tick events are handled in scheduling order. This is deterministic, but it is a modelling choice:
  within one tick, the order of events has no physical meaning.

## D16: Greedy and optimal-assignment dispatch over the oldest orders, with optional decision rounds
**Status:** Accepted (2026-11-04)
**Why:**
- The cost of a pairing is the robot's travel time to the pickup. The rest of the job takes the same
  time whichever robot does it, so it cannot change which matching is best.
- Both policies consider only the oldest orders, as many as there are idle robots, so they serve the
  same orders and a comparison measures matching quality alone. This also prevents starvation.
- Optimal assignment uses scipy's `linear_sum_assignment`. scipy and numpy are the project's first
  runtime dependencies. Writing our own Hungarian algorithm would add risk without adding anything to
  demonstrate; a brute-force oracle test checks the results instead.
- Called at every event, nearly every decision has one robot or one order, and then optimal equals
  greedy. Optional decision rounds (`decision_interval_ticks`) let robots and orders accumulate so that
  matching can matter.

**Consequences:**
- Decision rounds add up to one interval of waiting before an order is assigned. The KPI comparison
  must show whether better matching outweighs that.
- Neither policy looks ahead: busy robots that are about to finish, and orders not yet released, are
  ignored. Look-ahead belongs to the CP-SAT phase (M3).
- `scipy-stubs` is a development dependency, so mypy strict can check the calls into scipy.

## D17: KPIs come from the event log; P95 by nearest rank; comparisons are paired over seeds
**Status:** Accepted (2026-11-04)
**Why:**
- A single source of truth: KPIs are recomputed from the log, never tracked separately inside the
  simulator.
- Waiting time ends when a robot reaches the pickup, which is when service starts from the order's
  point of view. Assignment delay is reported separately, because decision rounds change it directly.
- Nearest-rank percentiles always return an observed value and are simple to state precisely.
- Running every variant on the same seeds and comparing per-seed differences removes order-stream
  variation from the comparison (common random numbers, D14).

**Consequences:**
- Time KPIs cover delivered orders only. Undelivered orders are counted, and make the comparison script
  fail rather than flatter an average.
- Throughput is measured up to the last event. When the fleet keeps up with demand, it simply equals
  the arrival rate; completion time is then the KPI that separates policies.
- Twenty seeds resolve differences of roughly a second in mean completion time for these
  configurations. Smaller effects need more seeds.

## D18: M3 plans a bounded horizon over all robots and commits only the next job of each idle robot
**Status:** Accepted (2026-11-04)
**Why:**
- M2 measured that per-decision matching equals greedy, because almost every event-driven decision has
  one robot or one order (D16, benchmarks.md). A better decision needs more information or more choice,
  not a better solver for the same choice.
- **Look-ahead.** Robots never interfere in this model, so a busy robot's finishing time and place follow
  exactly from its state. Planning busy robots too lets an order wait for a robot that will be closer
  soon.
- **Sequencing.** Planning several orders per robot lets a robot that frees up take a nearby order
  rather than the oldest one.
- **Receding horizon.** Future orders are unknown, so a long plan goes stale. Committing only the first
  job of each idle robot and re-planning at the next decision uses the plan's foresight without being
  bound to it. This is the rolling-horizon idea used in model predictive control.
- **Objective: total completion time** of the planned orders. The time from pickup to delivery (loading,
  loaded trip, unloading) is the same whichever robot does it, so this is equivalent to minimising total
  waiting time.
- **Window: the oldest K pending orders.** This bounds the problem size. As in D16, older orders are
  always considered first, so newer orders cannot push an older one out of the plan.

**Alternatives considered:**
- Optimal matching with predicted availability, one job per robot: look-ahead without sequencing. The
  insertion heuristic covers this case and more.
- The OR-Tools routing solver (vehicle routing with pickups and deliveries): a natural fit, but it is a
  metaheuristic with no optimality bound, and it would hide the model that this phase is meant to show.
- Planning with future orders: impossible online without a forecast; out of scope.

**Consequences:**
- A robot may stay idle while an order waits, because the plan gives that order to a busy robot. This is
  intended; the KPIs show whether it pays off.
- The window limits starvation but does not rule it out: an old, distant order can keep being sequenced
  last. The comparison reports the maximum completion time to show this.
- The availability prediction is exact only while robots never interfere. With traffic (M4) it becomes
  an estimate.
- Each decision now costs a planning solve. Solve time is measured and reported.

## D19: Cheapest insertion is the planning baseline; CP-SAT must beat it on the same model
**Status:** Accepted (2026-11-04)
**Why:**
- Comparing CP-SAT only with FIFO greedy would mix two effects: the planning model (look-ahead,
  sequencing, the objective) and the exact search. A heuristic on the same model separates them.
  Insertion against greedy measures the model; CP-SAT against insertion measures the search.
- Cheapest insertion is the standard construction heuristic for routing and scheduling. It is simple,
  deterministic, costs O(K² · (K + R)) per decision, and always finds a feasible plan here (D18).
- Orders are inserted oldest first, so older orders choose their place first.
- The insertion plan is also CP-SAT's starting hint and its fallback (M3.3), so CP-SAT never ends up
  with a worse plan than the heuristic.

**Consequences:**
- Insertion is myopic and never moves an order once placed. It found the optimum in 220 of 300 random
  small test problems.
- No local search (moving or swapping orders) for now. If CP-SAT gains little over insertion, local
  search is the cheaper thing to try next (Phase 2 backlog).

## D20: CP-SAT with one giant-tour circuit, one worker and a deterministic time limit
**Status:** Accepted (2026-11-04)
**Why:**
- **CP-SAT** solves the D18 problem exactly for small windows. When it stops early, it still reports a
  proven lower bound, so the remaining gap is known. The problem maps directly onto CP-SAT's circuit
  constraint with enforced time constraints.
- **One giant-tour circuit**, through every robot node and order node, with each order's robot pinned
  by `owner` constraints. This breaks the symmetry between equivalent circuits, so each plan is exactly
  one circuit. It has about (R + n)² literals, against R · n² for one circuit per robot. On 165 recorded
  decisions it needed 92 s in total, against 144 s for the per-robot version, with identical optimal
  objectives (a development measurement; algorithms section 11.3).
- **Reproducibility.** One worker, a fixed seed, and `max_deterministic_time` instead of a wall-clock
  limit. The same decision always gives the same plan, so simulation runs stay reproducible. Eight
  workers in CP-SAT's deterministic interleaved mode were slower in the same measurement. A stronger
  linear relaxation (`linearization_level` 2) proved fewer decisions optimal (99 against 123 of 165).
- **Limit: 0.2 deterministic seconds.** On the 165 decisions, the result was as good as the best plan
  found by any setting in 157 cases at 0.2, against 162 at 1.0, for less than half the solve time. The
  difference left is 916 ticks of planned completion time over the 165 decisions, about 5.6 ticks
  (0.56 s) per decision, summed over the orders it plans. Insertion alone was 24,351 ticks off.
- **Hint and fallback.** The insertion plan is the starting hint, and it is kept when CP-SAT finds
  nothing better. So, by the plan objective, CP-SAT never makes a decision worse than insertion would.

**Consequences:**
- Many solves stop at the limit without a proof (status `limit`). They are counted and reported with
  their gaps, not hidden.
- Deterministic time is not wall time, and the ratio between them varies. In the same measurement, single
  solves took up to 1.9 wall seconds at the 0.2 limit and up to 5.2 s at 1.0. Solve times are reported
  in wall seconds, as measured.
- OR-Tools becomes a runtime dependency, which also installs protobuf, pandas, absl-py and
  immutabledict. Results may differ between OR-Tools versions, so the benchmarks record the version.

## D21: The M3 comparison is paired against greedy and against insertion, and runs in parallel processes
**Status:** Accepted (2026-11-04)
**Why:**
- Two questions, two baselines (D19). Insertion against greedy shows what the planning model is worth;
  CP-SAT against insertion shows what exact search adds. Both are paired over the same 20 seeds and the
  same configurations as in M2, so the results can be read next to the M2 table.
- The maximum completion time is reported next to the mean and P95, because look-ahead and sequencing
  can starve a distant order (D18), and a mean or a P95 can hide that.
- Every CP-SAT decision is counted by outcome: no search needed, proven optimal, stopped at the limit
  (with its remaining gap), or fallback to insertion. Solver shortfalls are therefore visible.
- Runs execute in parallel worker processes. A CP-SAT solve is single-threaded with a deterministic
  limit, so the KPIs are the same with any number of processes.

**Consequences:**
- Solve wall times are measured while the processes share the CPU, so they overstate the time of a
  solve run alone. The output states the number of processes.
- The shared statistics helper (`metrics.compare.paired_difference`) now also serves the M2 script.
  Its output is unchanged: checked by diffing the old and new versions on 3 seeds.

## D22: A worst-first objective: the longest lead time first, then the total completion time
**Status:** Accepted (2026-11-05)
**Why:**
- M3.4 measured that minimising total completion time starves some distant orders. At 5 robots and 150
  orders/h, CP-SAT's worst completion time was 625 s against 415 s for greedy. The objective cannot
  see how waiting is distributed.
- **Lead time** is the time an order has already waited plus its planned completion time. Minimising
  the longest planned lead time first gives the order that has waited longest the earliest finish the
  fleet can offer. Among those plans, the total completion time is then minimised. Re-planned at every
  decision, this always protects the order that has waited longest.
- **No parameter to tune.** The M3 plan sketched due times with a lateness term. That needs a service
  target and a lateness weight, both chosen by hand, and the result depends heavily on them. If every
  order is already late, for example, total lateness is just total completion time minus a constant,
  and nothing changes. The worst-first objective needs neither. Its weight is computed from the
  problem (`worst_weight`).
- **One number for both planners.** The lexicographic order is encoded as weight × W + Σ C, where W is
  the longest lead time. The weight is n × U + 1, and U bounds every completion time, so any
  improvement of W outweighs any change of Σ C. Insertion and CP-SAT minimise the same `plan_cost`,
  so the D19 comparison still compares like with like.

**Alternatives considered:**
- Due times with weighted lateness: two hand-chosen parameters (see above).
- Age weights, minimising Σ (1 + age / τ) · C: smooth, but adds a parameter τ to tune.
- The sum of squared lead times: penalises long waits directly, but makes the objective quadratic.

**Consequences:**
- Only the single worst order is protected at each decision. The next-worst one is protected once it
  becomes the worst.
- The fair variants give up part of the gain in mean completion time. The comparison measures how
  much.
- With the weight, CP-SAT's objective has much larger coefficients. The gaps reported for stopped
  solves are relative to this weighted cost, so they are not comparable with the gaps of the
  total-completion variant.

## D23: Traffic resources are nodes and corridors, each holding one robot; time intervals are exact
**Status:** Accepted (2026-11-05)
**Why:**
- **Nodes.** A robot at node v from its arrival tick a to its departure tick d occupies v during the
  *closed* interval [a, d]. Two robots may never share a node at the same tick, so the next robot can
  arrive at d + 1 at the earliest. That leaves one tick (0.1 s) of clearance.
- **Corridors.** A robot driving from u to v, departing at d and arriving at d + τ, occupies the
  corridor {u, v} during the *open* interval (d, d + τ). The corridor is the same resource in both
  directions (D8 deferred this to now). One robot per corridor rules out head-on and swap conflicts,
  and also a robot catching up with another on the same corridor.
- **Integer ticks (D6)** make every overlap test exact. The open/closed convention makes "one robot
  leaves the corridor at v at tick t, another enters it from u at tick t" legal, because they are at
  different nodes. "One leaves node u at t, another arrives at u at t" is illegal.

**Consequences:**
- This is conservative. A two-metre aisle segment carries one robot at a time, even when two robots
  could safely follow each other. Throughput limits caused by this are part of the measured cost of
  congestion, not hidden.
- Robots are points that occupy nodes and corridors, with no footprint, acceleration or turning
  (ARCHITECTURE, "not modelled"). Certified safety functions remain out of scope.

## D24: Prioritized itinerary planning with SIPP, holding spots on dead-end endpoints, and moves off needed endpoints
**Status:** Accepted (2026-11-05)
**Why:**
- **Prioritized planning.** Robots plan one at a time, in decision order, against all existing
  reservations. This is first come, first served. It is not optimal for the fleet, but it is simple
  and fast, and it is the standard baseline in the lifelong multi-agent pickup-and-delivery literature.
  That setting is lifelong multi-agent path finding with task assignment, as in Ma et al.'s Token
  Passing (AAMAS 2017).
- **Whole itinerary at assignment.** Pickup and load, drop-off and unload, then a holding spot. All
  the reservations of a job, except the final hold, are therefore finite and known when it is planned.
  A station's next visitor can be planned right away, to arrive after the previous robot has left.
- **Holding spots.** After unloading, a robot drives to the nearest free endpoint (a dead-end spur)
  and holds it until its next plan. Stations are never used for parking, so they never stay blocked.
  An idle robot standing on a pickup that another robot is assigned to is first moved to another free
  endpoint.
- **Why planning cannot fail (D9 layout, the well-formed infrastructure of Čáp et al., ICAPS 2015).**
  - Endpoints are leaves, so no path passes through another robot's endpoint.
  - Every reservation except the holds at endpoints is finite.
  - A plan's goals are endpoints that no one else holds or plans to visit.
  - So after the last finite reservation everything is free, and SIPP always finds a path.
  - There are always free endpoints: the large map has 221 for at most 10 robots.

  A planning failure is therefore a bug, and it raises an error.
- **SIPP rather than a search over (node, tick).** At 0.1 s ticks, an edge takes 10 to 40 ticks, and a
  search over (node, tick) would expand every tick of every wait. SIPP searches over safe intervals:
  maximal time windows in which a node is free. It finds the earliest arrival, because arriving earlier
  in an interval is never worse when waiting is allowed (Phillips and Likhachev, ICRA 2011).

**Consequences:**
- The holding-spot move after each delivery adds a short trip. It is part of the measured traffic
  cost.
- Orders whose pickup is reserved by a busy robot are hidden from the dispatch policies until it is
  free. Among orders that share a pickup, only the oldest is offered. So a decision never sends two
  robots to one pickup.
- The simulator executes plans exactly, because it has no disturbances yet. Robustness to delays,
  where plans and execution diverge, is a stretch goal (ROADMAP M4, increment 5).
