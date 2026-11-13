# Algorithms

This document explains what each algorithm in the engine does, why it is correct, what it costs, and
where it stops being enough. Code references point to `src/amr_optimizer/`.

---

## 1. The warehouse graph (`warehouse/model.py`)

**Model.** The warehouse is a directed graph G = (V, E):
- **Nodes** are navigable locations with coordinates in metres: intersections, storage locations,
  stations, chargers and parking spots.
- **Edges** are traversable connections with a length in metres. A two-way corridor is two edges (D8).

**Representation.** An adjacency list: each node maps to the tuple of its outgoing edges.
- Memory is O(V + E), and listing a node's neighbours takes O(1).
- An adjacency matrix would need O(V²) memory. Warehouse graphs are sparse (degree ≤ 4 or so), so the
  matrix would be almost entirely empty.

**Invariants**, checked once in the constructor. The graph is read-only, so they then hold for its
whole lifetime.

| Invariant | Why it matters |
|---|---|
| Unique node ids | Every lookup is unambiguous |
| Every edge's endpoints exist | No route can lead to a node that isn't in the map |
| At most one edge per ordered pair | A route (a sequence of nodes) identifies its edges unambiguously |
| Edge length > 0 | No zero-time moves ("teleporting") in the simulator (D5) |
| Edge length ≥ straight-line distance | Keeps the Euclidean A* heuristic admissible and consistent (D5) |

**Generated layouts** (`warehouse/generator.py`):
- Alternating one-way aisles, plus two-way cross aisles at the bottom, the top and optional middle rows.
- Every endpoint (storage, station, charger, parking) is a **dead-end spur** off an aisle, so a robot
  standing at one never blocks through traffic. This is the *well-formed infrastructure* condition from
  the multi-robot planning literature (Čáp et al., ICAPS 2015; citation to be checked before M4). It
  lets prioritized multi-robot planning succeed where it would otherwise deadlock.
- Real warehouses are usually laid out this way, for the same reason.

---

## 2. Dijkstra's algorithm (`routing/dijkstra.py`)

**Problem.** Find the shortest route from a source to a target, where every edge has a length ≥ 0.

**Idea.** Grow a region of *settled* nodes outwards from the source, always settling next the closest
node not yet settled.

```text
dist[source] = 0; queue = {(0, source)}
while queue is not empty:
    (d, u) = pop the smallest entry
    if u is settled: skip it          # outdated entry (lazy deletion)
    settle u with distance d
    if u == target: stop
    for each edge u -> v of length w, with v not settled and the edge not blocked:
        if d + w < dist[v]:
            dist[v] = d + w; parent[v] = u; push (d + w, v)
```

**Why it's correct.** The key invariant: when a node is popped for the first time, its distance is final.
- Suppose some route to `u` were shorter than `d`.
- That route must leave the settled region at some node still in the queue, whose distance is ≥ `d`,
  because `u` was the smallest entry.
- Edges can only add length, so the route can't end up shorter than `d`.
- The argument fails with negative edge lengths, which the graph rules out.

**Complexity.**
- Time: O((V + E) log V), with one heap push per successful edge relaxation.
- Memory: O(V + E).

**Implementation choices.**
- **Lazy deletion.** `heapq` has no decrease-key operation, so an improved distance is pushed as a new
  entry and outdated entries are skipped when popped. This is simpler and, in Python, usually faster than
  maintaining an indexed heap.
- **Deterministic tie-breaking.** Each heap entry is `(distance, insertion counter, node)`. Equal-length
  alternatives always resolve the same way, the first one found wins, and nodes are never compared to
  each other (D7).
- **Early exit.** `shortest_path` stops as soon as the target is settled.
  `shortest_path_lengths` runs to completion and returns the distance to every reachable node. That is
  the one-to-all table dispatch needs: it answers "how far is every robot from this pickup?" with a
  single search.
- **Blocked edges** are passed in per query and the graph is never changed. A blocked aisle is an
  operational state, not a map edit.
- **An unreachable target** returns `route=None`. **An unknown node id** raises an exception (D4).

---

## 3. A* search (`routing/astar.py`, `routing/heuristics.py`)

**Idea.** Dijkstra explores in every direction. A* orders the queue by `f = g + h`:
- `g` is the length travelled from the source;
- `h` is a heuristic estimate of the length remaining to the target.

Nodes that lead away from the target get a large `f` and are expanded late, or never. With `h = 0`,
A* is exactly Dijkstra; a test checks that both expand the same nodes.

**When is A* optimal?**
- **Admissible:** `h` never overestimates the true remaining length.
- **Consistent:** `h(u) ≤ length(u → v) + h(v)` for every edge `u → v`. Consistent implies admissible.
  It also means the first expansion of a node is final, just as in Dijkstra, so a closed set without
  re-expansion is correct.
- Our implementation never reopens a node, so it requires a **consistent** heuristic (D10).

**Heuristics.**

| Heuristic | Value | Consistent when |
|---|---|---|
| `zero` | 0 | Always (equivalent to Dijkstra) |
| `euclidean` | Straight-line distance | Always, on any valid map |
| `manhattan` | \|dx\| + \|dy\| | Every edge is at least as long as the Manhattan distance between its endpoints, e.g. axis-aligned grids such as the generated maps |

Why `euclidean` is always consistent: for an edge u → v,
`|u − t| ≤ |u − v| + |v − t| ≤ length(u → v) + |v − t|`. The first step is the triangle inequality;
the second is the map invariant length ≥ straight-line distance.

**Tie-breaking.** When `f` values are equal, the entry with the smaller `h` wins, i.e. the node closer to
the target. On grids, where many routes have equal length, this avoids expanding many equally good
alternatives.

**What goes wrong with a bad heuristic.** A heuristic that overestimates can return a longer route.
- `test_inflated_heuristic_can_return_a_longer_route` shows this: three times the straight-line
  distance returns an 8 m route instead of the 7.21 m one.
- Deliberately inflating the heuristic ("weighted A*") is a legitimate speed-for-quality trade-off, but it
  must be a visible choice, never a silent default.

**Complexity.** The worst case is the same as Dijkstra's, since a useless heuristic expands everything.
The typical benefit is measured in [benchmarks.md](benchmarks.md). On the large map, Manhattan A*
expands 3.4× fewer nodes than Dijkstra on average, and the gain is smaller for routes that must detour
around one-way aisles.

---

## 4. How correctness is checked

- **Hand-computed cases** on tiny graphs (`tests/conftest.py`): straight line, two branches, one-way
  detour, disconnected nodes, exact ties, blocked edges, source equal to target.
- **Oracle tests** (`tests/oracle/`). On 60 seeded random geometric graphs, with and without randomly
  blocked edges, our results must match NetworkX's: route lengths, unreachability, and one-to-all
  distances. A guard test checks that the random graphs include both reachable and unreachable queries.
- **Cross-checks.**
  - A* must match Dijkstra's route length on every query.
  - With the zero heuristic, A* must expand exactly the same nodes as Dijkstra.
  - Euclidean A* must never expand more nodes than Dijkstra.
- **`validate_route`** is applied to every returned route. It checks that every node exists, that edges
  are used in their direction of travel, that no blocked edge is used, and that the stated length is
  correct. The simulator will reuse it as an invariant check.
- **Map regression.** The committed example maps must match the generator exactly.

---

## 5. Time in integer ticks (`domain/timing.py`)

**Problem.** The simulator, and later the conflict checks, compare times: is this node free at time t,
do these two intervals overlap? With floating-point seconds those tests break on rounding noise
(`0.1 + 0.2 != 0.3`), and a check that passes on one map can fail on another.

**Approach.**
- All simulation time is an `int` count of ticks. `TimeModel(tick_s)` fixes how long one tick lasts.
  Seconds appear only at the boundaries: configuration in, reported KPIs out.
- A duration in seconds becomes ticks by rounding **up** (`to_ticks`), so a plan never assumes a robot
  arrives earlier than it physically could.
- An edge's travel time is `max(1, ceil(length / speed / tick))`. The minimum of one tick means every move
  takes time, the time analogue of D5.
- **Floating-point noise is not rounded up.** A value within a relative 1e-9 of a whole number snaps to
  it. Without this, 5.4 m at 1.2 m/s, which is exactly 45 ticks of 0.1 s, evaluates to
  45.00000000000001 and would cost 46 ticks.
- `TravelTimes` computes every edge's ticks once, in O(E), for one graph and one speed. A route's
  duration is the sum of its edges' ticks. `arrival_offsets` gives the tick at which the robot reaches
  each node of the route.

**Invariants** (tested):
- Every edge takes at least one tick.
- Travel ticks never underestimate, and overestimate by less than one tick. In symbols,
  (ticks − 1) × speed × tick < length ≤ ticks × speed × tick, checked on 4,000 seeded random cases.
- Every decimal duration k/10 s is exactly k ticks of 0.1 s, and ticks → seconds → ticks is the identity
  for five different tick lengths.

**Why round each edge separately, and what it costs (D12).** A robot has to reach every node at a whole
tick, because that is what a reservation table (M4) indexes. So each edge is rounded on its own, and a
route can take up to one tick per edge longer than its total length converted at once. As a result,
**the shortest route in metres is not always the fastest in ticks.** The test example: A → B → D is 2.2 m
but 2 + 2 one-second ticks, while the curved direct edge A → D is 2.5 m but 3 ticks. Routing still
minimises metres; `TravelTimes.is_exact` reports whether any edge needed rounding. On all three generated
presets at 1 m/s and 0.1 s ticks, no edge is rounded: every edge length is a whole number of decimetres.
Metres and ticks then pick the same routes.

**Choosing the tick.** A shorter tick loses less to rounding but makes every time-indexed structure
larger: reservation tables and space-time search states grow with the number of ticks. The planned
default is 0.1 s; at 1 m/s, one tick is 10 cm of travel.

**Limitations.** Constant speed: no acceleration, braking or turn time (see ARCHITECTURE.md). One
`TravelTimes` table per speed, so a fleet with several robot speeds would need one table per speed class.

---

## 6. Robots, orders and the robot state machine (`domain/robots.py`, `domain/orders.py`)

**Problem.** The simulator must never put a robot into an impossible situation, such as loading away
from the pickup, carrying two orders, or finishing a move before it could have arrived. Bugs like these
don't crash. They quietly produce wrong KPIs.

**Model.**
- A `TransportOrder` is immutable: carry one load from `pickup` to `dropoff`. It becomes known at
  `release_tick` and cannot be assigned earlier.
- A `RobotState` is an immutable snapshot of one robot: its phase, its node (or, while travelling, the
  node it left), its order, its route, and the ticks at which the phase began and ends.

**The state machine.** A transport job takes a robot through five phases. `ALLOWED_TRANSITIONS` lists
exactly these changes, and every other one raises `InvalidTransitionError`:

| From | Method | To | Also requires |
|---|---|---|---|
| IDLE | `assign(order, route)` | TO_PICKUP | the order is released; the route runs from the robot to the pickup |
| IDLE | `assign(order, single-node route)` | LOADING | the robot already stands at the pickup |
| TO_PICKUP | `arrive` | LOADING | the tick is the planned arrival |
| LOADING | `finish_loading(route)` | TO_DROPOFF | the tick is the end of loading; the route runs to the drop-off |
| TO_DROPOFF | `arrive` | UNLOADING | the tick is the planned arrival |
| UNLOADING | `finish_unloading` | IDLE | the tick is the end of unloading; the order is delivered |

**Design choices.**
- **Immutable states.** Each transition method returns a new state. A dispatcher can be handed a
  snapshot of the fleet that cannot change underneath it, and an event log can keep old states as they
  were.
- **Invariants are checked in the constructor**, so a state that breaks one cannot exist:
  - an idle robot has no order, no route and no end tick, and every other phase has an order and an end;
  - loading happens at the pickup, unloading at the drop-off;
  - only a travelling robot has a route; the route has at least one edge, starts at the robot's node and
    ends at the pickup (TO_PICKUP) or the drop-off (TO_DROPOFF);
  - a phase never ends before it begins, and travel takes at least one tick.
- **Every non-idle phase knows when it ends** (`until_tick`). The simulator sets it when the phase
  starts: travel time from `TravelTimes`, loading and unloading times from the scenario. A transition at
  any other tick is refused, so a scheduling bug fails loudly at the event where it happens.
- **Order progress is read from the robot that carries the order** (D13). Waiting, being carried and
  being delivered correspond to the robot's phases, so there is no second state machine to keep in sync.

**Cost.** Every transition and every invariant check is O(1). Routes are shared, not copied.

**Testing.** All 20 combinations of phase and transition method are tried: exactly the 5 legal ones
succeed. Further tests check that the table is exactly the one above, that every phase is reachable from
IDLE and leads back to IDLE (no dead ends), and that 11 kinds of inconsistent state are each rejected
with a specific message.

**Limitations (MVP).** No preemption or reassignment: an assigned order stays with its robot until it is
delivered. No fault, charging or parking phases. An idle robot stays at its last drop-off. One load per
robot.

---

## 7. Scenarios and seeded order streams (`scenario/`)

**Problem.** Dispatch policies can only be compared fairly on identical inputs, and a result is only
credible if anyone can regenerate the input from a seed. The input is a map, a fleet, timing
parameters and a stream of orders.

**`Scenario`** holds everything one simulation run needs, and is validated as a whole:
- robots start idle at tick 0, each on its own node, with unique ids;
- orders have unique ids and are sorted by release tick;
- every node a robot or order mentions exists on the map;
- speed is positive, and loading and unloading times are non-negative.

**Arrivals: a Bernoulli process.** In each tick, an order is released with probability
p = rate × tick length. This is the discrete-time counterpart of the Poisson process: counts are
binomial instead of Poisson, and gaps between orders are geometric instead of exponential. Both converge
to the Poisson case as p gets small, and p is small at realistic rates: about 0.0017 at 60 orders per
hour with 0.1 s ticks. Why not sample exponential gaps in seconds?
- Release times are already whole ticks, so nothing is rounded.
- The exponential sampler calls `log`, which comes from the platform's maths library. The Bernoulli
  process uses only `random()`, so the stream doesn't depend on the platform.

**Order content.** Each order is a retrieval: a uniformly random storage location to a uniformly random
station.

**Fleet placement.** Robots start as at the beginning of a shift: parking spots first, then chargers,
both in id order. Larger fleets take further start nodes at random from the storage locations. Stations
stay free.

**Separate random streams (D14).** Orders and placement each draw from their own `random.Random`,
seeded from the scenario seed with a different label. Changing the fleet size therefore leaves the
order stream unchanged, so comparisons across fleet sizes are *paired*: every fleet faces exactly the
same orders. This is the "common random numbers" technique, which removes order-stream noise from the
difference between two configurations.

**Cost.** O(duration in ticks) to draw the arrivals, plus O(1) per order. Measured on the development
laptop: about 0.8 ms per 30-minute stream (18,000 ticks).

**Testing.**
- One scenario is pinned: its order count, first four orders and robot positions. CI runs that test on
  both Windows and Linux, so a platform difference would make it fail.
- The same seed gives an identical scenario, and a different seed gives a different one.
- Changing the fleet size from 1 to 25 robots leaves the orders unchanged.
- Over 100 seeds at 120 orders per hour for 30 minutes, the mean order count must lie within 3 standard
  errors of the expected 60. Measured mean: 60.87 orders, which is 1.1 standard errors away. Measured
  standard deviation: 7.51, against 7.73 for the binomial model.

**Limitations.**
- Retrievals only: no put-away orders, no pallet returns.
- Uniform choice of locations: no popularity skew (ABC classes), no waves or peaks over the shift.
- At most one order per tick. Rates that would need more are rejected rather than silently capped.

**Scenario files (`scenario/io.py`, D32).** Generated scenarios cannot express a particular
warehouse, fleet or order list. A scenario file can, and it lifts the limits above: any order list,
put-aways included, waves and peaks, several orders in one tick.
- **Content.** A map (a preset name, a map file, or a map written inline), each robot's start node,
  and every order with its release time in seconds. Speed, tick length and handling times are
  optional, with the defaults of generated scenarios.
- **Parsing is strict**, as for maps: a missing, unknown or mistyped field raises an error that
  names its location, such as `orders[3].release_s`. Times are rounded up to ticks (D12). Orders
  are sorted by release tick; the sort is stable, so orders released together keep the file's
  order, which is the order dispatch sees them in.
- **Then the usual validation:** the map's own (section 1), each order's, and the scenario's as a
  whole (unknown nodes, duplicate ids, robots sharing a node).
- **Cost:** linear in the file size, plus building the map.
- **Testing.** Every field's errors, each with its location; maps by preset, relative path,
  absolute path and inline; rounding and sorting; and every example in `scenarios/examples/` runs
  under greedy and `cpsat-fair` with traffic, delivering every order without conflicts.

---

## 8. Discrete-event simulation (`sim/`)

**Problem.** Plan cost alone cannot compare dispatch policies. What matters is what happens when the
plan is executed over time: how long orders wait, how busy robots are, how many orders per hour get
delivered. The simulator executes decisions and records what happened, exactly and reproducibly.

**Why discrete-event, not fixed time steps?** Nothing changes between events: a robot driving a route
of known duration needs no updates until it arrives. A discrete-event simulator jumps straight from one
event to the next, so its cost grows with the number of events, not with the number of ticks. A
fixed-step loop would visit each of the 36,000 ticks in an hour at 0.1 s per tick.

**Events.** There are only two kinds:
- an order is **released** and becomes pending;
- a robot's current phase **ends**: it arrives at the pickup, finishes loading, arrives at the drop-off,
  or finishes unloading. The robot then moves to its next phase through the state machine (D13).

A busy robot always has exactly one scheduled event, at the end of its current phase (`until_tick`).

**The loop.** Events wait in a binary heap of `(tick, sequence number, event)` entries. The sequence
number breaks ties in scheduling order, which keeps runs deterministic and means Python never has to
compare two events.
1. Take the earliest tick, and handle every event scheduled for it.
2. If an order is pending and a robot is idle, call the dispatch policy.
3. Executing an assignment can create more events at the same tick, for example a robot that already
   stands at the pickup with zero loading time. Handle those and call the policy again before time
   moves on.
4. Check the invariants, then continue with the next tick that has events.

**Dispatch interface.** A policy is a pure decision function. It receives a `DispatchSnapshot`: the
tick, every robot's immutable state, the pending orders in release order, and a `TravelPlanner` for
route and time queries. It returns assignments. The simulator validates the whole decision before
executing any part of it: each robot must be idle, each order pending, neither may appear twice, and
both the pickup and the drop-off must be reachable. A policy may leave robots idle and orders
waiting.

**`TravelPlanner`.** Computes the shortest route (by length, D12) between two nodes once, with
Euclidean A*, and caches it together with its travel ticks. The map is static in M2, so cached answers
stay valid. Dispatch asks about the same robot-to-pickup pairs over and over, so most queries become
dictionary lookups.

**Invariants**, checked after every tick (a failure raises `SimulationInvariantError`):
- every released order is in exactly one place: pending, carried by exactly one robot, or delivered;
- each busy robot has exactly one scheduled event, at the end of its phase, and each idle robot none;
- each phase lasts exactly its travel time (from `TravelTimes`), loading time or unloading time;
- every route a robot starts passes `validate_route`;
- time never goes backwards.

The state machine adds its own checks at every transition (D13).

**Event log.** Each order produces six entries: released, assigned, pickup reached, loaded, drop-off
reached, delivered. Each entry records the tick, node and robot; assigned and loaded entries also record
the route length that starts there. Every KPI is computed from this log (next increment).

**Cost.** O(log n) per event for the heap, plus the policy's own cost. The invariant check is O(robots)
per tick that has events. Measured on the development laptop: a one-hour scenario on the large map with
10 robots and 282 orders took 0.086 s (best of 3), with a first-come-first-served test policy, the
invariant checks and the route computations included.

**Testing.**
- Hand-computed timelines on a three-node line: a single job tick by tick, a robot already at the pickup,
  zero handling times (four events chained within one tick), queueing behind a busy robot, two robots in
  parallel.
- Invalid decisions are rejected: unknown or busy robots, orders that aren't pending, duplicates,
  unreachable pickups and drop-offs.
- The checks for order accounting, double carrying, missing or extra scheduled events and phase
  durations are each triggered on purpose, to show that they fire.
- On 24 generated runs (2 maps × 1, 3 and 6 robots × 4 seeds):
  - every order goes through all six steps in order;
  - every duration equals the time computed independently from Dijkstra's route length;
  - every robot does one job at a time, and starts each job where the last one ended (no teleporting).

**Limitations (M2).**
- In the default free-flow mode, robots don't interact: there are no collisions, no blocking and no
  waiting for each other. That was the MVP simplification. Since M4, `traffic=True` makes robots plan
  around each other (section 13.3), and the difference in KPIs measures the cost of congestion.
- The policy is only called when something changes, or, with decision rounds, at each round (D16).
- No faults, blocked edges or replanning (Phase 2).

---

## 9. Dispatch: greedy and optimal assignment (`dispatch/`)

**Problem.** At a decision point, some robots are idle and some orders are pending. Which robot takes
which order? A job's loading, loaded trip and unloading take the same time whichever robot does it.
So the only part that depends on the choice is the robot's empty trip to the pickup, and that is what
both policies minimise.

**Greedy** (`GreedyDispatch`). Take the pending orders oldest first, and give each one the idle robot
that reaches its pickup soonest; ties go to the robot listed first.
- Cost per decision: O(P + R · min(R, P)) cached travel-time lookups, for P pending orders and R idle
  robots.
- It is myopic. Giving the oldest order its nearest robot can leave the next order with a robot far
  away. The unit test case: robots at N1 and N4, orders picking up at N2 (older) and N0. Greedy needs
  1 + 4 = 5 edges of empty travel; the optimum needs 2 + 1 = 3.

**Optimal assignment** (`HungarianDispatch`). Take the oldest R serviceable orders, as many as there are
idle robots, and match them to the robots so that the total travel time to the pickups is as small as
possible. This is the *linear assignment problem*, classically solved by the Hungarian algorithm in
O(n³).
- **Solver.** scipy's `linear_sum_assignment`. According to its documentation, this is a modified
  Jonker–Volgenant algorithm with no initialization (Crouse, 2016): a shortest augmenting path method,
  also O(n³), and fast in practice. With R ≤ 25 robots, a decision takes far less time than routing.
- **Forbidden pairs** (a robot that cannot reach a pickup) are given a cost larger than the sum of all
  allowed costs. Any matching that uses fewer forbidden pairs is then cheaper, so the solver first
  maximises the number of allowed pairs and then minimises their total cost. The forbidden pairs are
  dropped afterwards.
- **Why only the oldest orders?** Both policies then serve the same orders and differ only in who takes
  which, so a comparison isolates the quality of the matching. It also prevents starvation. A matching
  over *all* pending orders would keep picking nearby orders and could leave a distant one waiting
  indefinitely; avoiding that would need an ageing term in the cost.
- **Optimal per decision, not over the run.** The matching knows nothing about orders that haven't
  arrived yet, or about busy robots that are about to finish.

**Why decision rounds matter (D16).** When the policy is called at every event, a decision almost always
has one side of size one:
- at low load, one new order meets several idle robots, and both policies pick the nearest robot;
- at high load, one robot that just became free meets a queue, and both policies give it the oldest
  order.

With one side of size one, the optimal assignment *is* the greedy choice. Optimal matching can only
help when several robots meet several orders. The simulator therefore offers optional **decision
rounds** (`decision_interval_ticks`): the policy is called only at multiples of the interval, so orders
and idle robots accumulate in between. The trade-off is that a round can add up to one interval of
waiting before an order is assigned. Measured on the large map, batching did not pay off: see
section 10 and [benchmarks.md](benchmarks.md).

**Testing.**
- `min_cost_matching` against a brute-force oracle: on 300 seeded random matrices of up to 5 × 5 with
  forbidden pairs, the result has the same number of pairs and the same total cost as the best
  assignment found by trying them all.
- The hand-computed myopic case: greedy 50 ticks of empty travel, Hungarian 30.
- On 200 seeded random decisions on the medium map, both policies serve the same orders, and Hungarian
  never needs more travel than greedy. Measured: Hungarian was strictly better in 44 of the 200
  decisions (139 of them had at least two possible pairs), and needed 5.4% fewer pickup ticks in total.
  This compares single decisions, not whole runs.
- Shared behaviour: spare robots stay idle, the oldest orders are served first, busy robots are never
  assigned, and unreachable orders and robots are skipped.
- Both policies deliver every order of a generated scenario, with and without decision rounds.

---

## 10. KPIs (`metrics/`) and the dispatch comparison

**Definitions (D17).** Every KPI is computed from the event log alone, in seconds of simulated time:
- **waiting time**: from an order's release until a robot reaches its pickup;
- **assignment delay**: from release until a robot is assigned; this isolates the cost of decision
  rounds;
- **completion time** (lead time): from release until delivery, as mean, P95 and maximum. The
  maximum was added in M3, to show starvation that a mean and a P95 can hide;
- **throughput**: delivered orders per hour, from tick 0 to the last event;
- **utilization**: the share of robot time between assignment and delivery;
- **empty and loaded distance**: metres driven to pickups, and with a load. The empty share is the part
  dispatch can influence;
- **conflicts** (since M4): pairs of robots in the same node or corridor at the same time, counted by
  the independent checker (section 13.1) in the recorded trajectories. With traffic the count must be
  zero. In free flow it measures the collisions that traffic management has to prevent;
- **decision latency** (since M5): the wall time of each dispatch decision, from building the
  snapshot to the end of executing it, including, with traffic, the planning of every itinerary. It
  is the only machine-dependent number in a run's result, and no decision reads it.

Time KPIs are means and P95s over *delivered* orders. Undelivered orders are counted separately and
make the comparison script fail, so they can never disappear from an average.

**P95 by nearest rank.** The P95 is the smallest observed value with at least 95% of the values at or
below it: the value at rank ⌈0.95 n⌉. Unlike interpolating definitions, it is always a value that was
actually observed.

**Paired comparison.** For each seed, all variants run on the same scenario (D14), and the comparison
uses per-seed differences. Seed-to-seed variation, such as a busy order stream, affects every variant
alike and cancels out. The 95% confidence interval of the mean difference uses the t distribution with
n − 1 degrees of freedom.

**Result (M2).** On the large map, optimal matching gave no measurable gain over greedy dispatch when
deciding at every event. 10 s decision rounds made both policies worse by 5 to 24 s of mean completion
time. See [benchmarks.md](benchmarks.md) for the table and its reading. The reason is structural:
event-driven decisions almost always have one robot or one order, and then the optimal choice is the
greedy one.

**Testing.**
- KPIs are checked on hand-computed runs: a single job, a queued job, an idle second robot, undelivered
  orders, and a run cut off by `max_tick`.
- The nearest-rank percentile is checked on known ranks.
- The command line is tested end to end, including JSON output, map files and argument errors.

**Per-order timeline (`metrics/timeline.py`).** KPIs summarise a run; a timeline shows each order:
which robot took it, and when it was released, assigned, reached, loaded, brought to its drop-off
and delivered, with its waiting and completion times and its empty and loaded distances.
`amr-sim --timeline FILE` writes it as CSV, one row per order in release order.
- **The KPI definitions, exactly.** Each step is the order's first log entry of that kind.
  Waiting and completion are tick differences converted once, as in `compute_kpis`, so the mean of
  the delivered rows' completion times is the completion KPI, and the distances add up to the
  distance KPIs.
- **Steps that did not happen are empty**, for example an order still waiting when the run ended,
  so an unfinished order is visible instead of missing.
- **Cost:** one pass over the event log.
- **Testing.** Two queued orders on the three-node line, every step checked against the hand
  timeline; undelivered and cut-off orders; and on generated runs with traffic, the rows' means
  and sums against the KPIs.

---

## 11. Bounded-horizon planning (`dispatch/horizon.py`, M3)

**Problem.** M2 showed that per-decision matching equals greedy (section 10). Two kinds of choice are
missing from that decision:
- **Look-ahead.** A busy robot that will soon deliver near a new pickup may be a better choice than an
  idle robot that is far away.
- **Sequencing.** A robot that frees up while orders queue could take a nearby order instead of the
  oldest one.

### 11.1 The planning model

At each decision, a planning policy builds a `HorizonProblem` (D18):
- **All robots.** Each robot has a *ready time and node*. An idle robot is ready now, where it stands.
  A busy robot is ready when and where it delivers its current order: the rest of its current phase plus
  the fixed durations of the phases still ahead. Robots never interfere in this model, so this
  prediction is exact.
- **The oldest K pending orders** that can be planned.
- **Times as plain numbers**, in ticks from now:
  - each order's *job* time: loading, the loaded trip and unloading;
  - each robot's *start* trip to each pickup;
  - each *setup* trip, from one order's drop-off to another order's pickup.

A **plan** gives each robot a sequence of orders. If robot r takes orders j₁, j₂, … in that order, they
are delivered at
- C(j₁) = ready(r) + start(r, j₁) + job(j₁)
- C(j₂) = C(j₁) + setup(j₁, j₂) + job(j₂), and so on.

Robots never wait on purpose, because all times are fixed and a later start only delays later orders.

**Objective: minimise Σ C(j).** An order's waiting time is its delivery time minus its job time, plus
the time it had already waited at the decision. Both of those are the same in every plan, so minimising
total completion time also minimises total waiting time.

**Receding horizon.** Only the first job of each idle robot is carried out now. The rest of the plan,
including the jobs planned for busy robots, is planned again at the next decision. Future orders are
unknown, so a plan's later steps would go stale anyway.

**Worked example** (a unit test). A line N0–N4 with 10 ticks per edge, loading 5 ticks, unloading 7.
At tick 20, R1 is idle at N0, and R2 will be ready at N3 in 22 ticks. The older order O2 goes from N4
to N0 (job time 52); O3 goes from N2 to N4 (job time 32).

| Plan | O2 delivered | O3 delivered | Total |
|---|---:|---:|---:|
| R1 takes O2, R2 takes O3 (what greedy does) | 40 + 52 = 92 | 22 + 10 + 32 = 64 | 156 |
| R1 takes O3, R2 takes O2 (the optimum) | 22 + 10 + 52 = 84 | 20 + 32 = 52 | 136 |

The optimum leaves the far, older order for the busy robot, which will be one edge from its pickup in
22 ticks. Only "R1 takes O3" is committed now.

**Plannable orders.** On a map that is not strongly connected, some trips do not exist. `build_problem`
keeps an order only if it can be delivered, some robot can reach its pickup, and the trips to and from
every older planned order exist. Then a feasible plan always exists: one robot that can reach an order
can take all the orders, starting with that one. On generated maps nothing is ever skipped.

**Complexity.** Building the problem costs O(K · (K + R)) cached travel-time lookups. Evaluating a plan
costs O(K + R).

**Testing.**
- Availability in each busy phase, computed by hand.
- Availability against the simulator: on a whole generated run, every predicted delivery tick and node,
  at every decision, matches the actual delivery.
- The worked example: all six plans evaluated by hand.
- A brute-force oracle, in the tests only, tries every plan: n! · C(n + R − 1, R − 1) of them for n
  orders and R robots, each enumerated exactly once. It is the reference for the heuristic and the
  solver in the next increments.
- Malformed plans and inconsistent problems are rejected.

### 11.2 Cheapest insertion (`dispatch/insertion.py`)

**Approach.** Start with an empty sequence per robot. Take the orders oldest first, and insert each one
at the robot and position where it adds the least total completion time. Ties go to the robot listed
first, then to the earliest position.

**Cost.** K insertions. Each tries O(K + R) positions and re-evaluates one robot's sequence in O(K),
so a decision costs O(K² · (K + R)): about 2,000 elementary steps for K = R = 10.

**Always feasible.** A robot that can reach an order can take it first, and every trip between two
planned orders exists (section 11.1). So every order has at least one valid position.

**The policy** (`InsertionDispatch`). At each decision it plans every robot and the oldest 10 pending
orders (`DEFAULT_MAX_ORDERS`, D18), then starts each idle robot's first planned job. An idle robot with
no planned job waits.

**Why it is the baseline for CP-SAT (D19).** It uses the same model as CP-SAT: look-ahead, sequencing
and the same objective. Comparing insertion with greedy measures what the model is worth. Comparing
CP-SAT with insertion measures what exact search adds on top.

**Limitation: it is myopic.** Insertion never moves an order once it is placed. The unit test case:
- Two idle robots. O0 is 10 ticks from R1 and 11 from R2. O1 is 10 ticks from R1 and 100 from R2.
- Every job takes 10 ticks, and every trip between the two orders takes 50.
- Insertion gives the older O0 to R1 (20 < 21). O1 then adds 80 whether it goes on R1 or to R2, so
  the total is 100.
- The optimum swaps them, R2 taking O0 (21) and R1 taking O1 (20), for a total of 41.

**Testing.**
- The worked example of section 11.1: insertion finds the optimum (136) and commits "R1 takes O3",
  where greedy commits "R1 takes O2".
- 300 random problems with up to 5 orders and 3 robots: every plan is valid and never better than the
  brute-force optimum. Measured: insertion found the optimum in 220 of the 300.
- The per-robot sequence total agrees with the plan evaluator on every plan of 30 random problems.
- On a generated run, every committed job is delivered at exactly the planned tick. So the model and
  the simulator agree on start trips and job times, not only on availability.
- Every order of a generated scenario is delivered, with and without decision rounds, and two runs give
  identical event logs.

### 11.3 CP-SAT (`dispatch/cpsat.py`)

**Problem class.** Assigning n orders to R robots and sequencing them, with sequence-dependent setup
times (the empty trips) and the sum of completion times as the objective, is parallel-machine
scheduling with setup times, R | s_ij | ΣC_j. It is NP-hard, so exact solving is only realistic for
small windows, which is what the receding horizon provides.

**The model (a "giant tour").** One circuit visits R robot nodes and n order nodes: robot 0, its
orders in sequence, robot 1, its orders, …, and back to robot 0. One Boolean literal per arc:
- robot r → order j: j is r's first job, so C(j) ≥ ready(r) + start(r, j) + job(j);
- order i → order j: j follows i on the same robot, so C(j) ≥ C(i) + setup(i, j) + job(j);
- order j → robot r + 1: j is r's last job;
- robot r → robot r + 1: r does nothing.

Each time constraint applies only when its arc is chosen (CP-SAT's enforcement literals). The circuit
constraint makes the chosen arcs a single cycle through every node, which rules out sub-tours, so every
order is done exactly once. The objective is to minimise Σ C(j). Minimisation drives each C(j) down to
its earliest value, so the model's optimum is the best plan's cost. The plan is then read from the arcs
and evaluated again by the plan evaluator of section 11.1, which stays the single definition of cost.

**Breaking symmetry.** Without more constraints, the robots' segments could appear in the circuit in any
order: (R − 1)! circuits for the same plan, all with the same cost, which the search would have to rule
out one by one. An integer `owner(j)` per order, pinned by every arc, forces the robots into index order:
- a first job's owner is its robot;
- a follower has its predecessor's owner;
- the arc out of a last job must lead to the owner's successor.

Then each plan corresponds to exactly one circuit.

**Size.** About (R + n)² literals: robot → robot R, robot → order R·n, order → order n(n − 1), order →
robot n·R. A first version used one circuit per robot, with R · n² literals. On 165 decisions recorded
from one 10-robot run, the giant tour needed 92 s in total against 144 s for the per-robot model, with
identical optimal objectives. That comparison was a development measurement; the per-robot model is not
kept in the code.

**Hint, fallback and the trivial case.**
- The insertion plan is given to CP-SAT as a complete starting solution.
- If CP-SAT finds nothing better in time, the insertion plan is used, so a decision is never worse than
  insertion's by the plan objective.
- With one order or none, there is no search: insertion is then optimal.

**Reproducibility (D20).** One worker, a fixed random seed, and a *deterministic* time limit, which is
CP-SAT's measure of work done, not wall-clock time. The same decision therefore always gives the same
plan, and a whole simulation run is reproducible. A wall-clock limit would make plans depend on the
machine's load.

**What is hard.** Proving optimality, not finding good plans. A sum of completion times under
conditional precedence constraints has a weak linear relaxation, so the lower bound rises slowly. On the
same 165 recorded decisions (one worker; deterministic-time limit in CP-SAT's work units):

| Limit | Total solve time | Proven optimal | At the best plan found by any setting | Excess ticks over the best |
|---|---:|---:|---:|---:|
| 0.05 | 31 s | 106 | 139 | 4,246 |
| 0.2 | 64 s | 123 | 157 | 916 |
| 1.0 | 153 s | 133 | 162 | 135 |
| 0.2 with 8 workers (deterministic interleaved search) | 108 s | 117 | 156 | 1,204 |
| Insertion alone | | | 59 | 24,351 |

CP-SAT finds much better plans than insertion, and most of its remaining time goes into the proof.
- Eight workers in CP-SAT's deterministic parallel mode were slower here than one.
- A stronger linear relaxation (`linearization_level` 2) did worse: at the 0.2 limit it proved 99
  decisions optimal, against 123 with the default.
- The default limit is therefore 0.2, with one worker and the default linearization (D20).

Wall times were measured one solve at a time on the development laptop, and they are noisy. The 1.0
setting took 92 s in one run and 153 s in another, and the 0.2 setting 64 s and 42 s. The solver
outcomes are not noisy: the 0.2 setting proved the same 123 decisions optimal in both runs.

**Testing.**
- The myopic case of section 11.2: CP-SAT finds 41, where insertion gets 100, and proves it optimal.
- 250 random problems (2 to 5 orders, 1 to 3 robots) and 50 problems with real travel times on the
  medium map: CP-SAT proves optimality every time, and its objective equals the brute-force optimum.
- One order or none: no search, and the insertion plan is returned.
- The same problem solved twice gives the same plan; two simulation runs give identical event logs.
- With a near-zero time limit, the result is still a valid plan, no worse than insertion's, and its
  bound is at most its cost.
- On a generated run, every order is delivered, and for every decision, bound ≤ cost ≤ insertion's cost.

### 11.4 Results in simulation

See [benchmarks.md](benchmarks.md), "Planning". In short:
- **High load (about 90% utilization).** Insertion cut mean completion time by 18–20% against greedy,
  and CP-SAT by 24–26%. CP-SAT beat insertion by about 11 s, with intervals that exclude zero.
- **Moderate load.** Gains of 1–2 s.
- **Where it comes from.** Less empty travel: a planned robot takes a nearby order, or leaves an order
  to a busy robot that will be closer.
- **The cost.** The worst-case completion time grew under CP-SAT, because the objective doesn't care how
  waiting is distributed.

### 11.5 Worst first: protecting the order that has waited longest (D22)

**Problem.** Total completion time is indifferent to how waiting is distributed. A plan that makes one
distant order wait much longer, so that several near orders finish sooner, scores better. Re-planned
at every decision, that distant order can keep being sequenced last (section 11.4).

**Approach.** An order's *lead time* is the time it has already waited plus its planned completion
time. The worst-first objective (`Objective.WORST_FIRST`) minimises the longest lead time first, and
then the total completion time. It is used by the `insertion-fair` and `cpsat-fair` policies.

**One number to minimise.** `plan_cost` = weight × W + Σ C(j), where W is the longest lead time.
- Every completion time lies between 0 and U = `completion_upper_bound`. A robot never waits, so its
  last delivery comes at most its ready time plus, for every order, the longest possible approach and
  the job.
- Two plans' totals therefore differ by at most n · U. With weight = n · U + 1, any improvement of W
  outweighs any change of the total, so the order is lexicographic.
- In CP-SAT, a variable W ≥ waited(j) + C(j) for every order carries the worst lead time.
- Insertion and the brute-force oracle evaluate `plan_cost` directly.

**Worked example** (a unit test). One idle robot. Order A has waited 100 ticks, and its pickup is 50
ticks away. Order B has just arrived, right next to the robot. Each job takes 10 ticks, and the trip
between the two orders 50 either way.

| Sequence | Total completion | Longest lead time |
|---|---:|---:|
| B, then A (what total completion prefers) | 10 + 70 = 80 | A: 100 + 70 = 170 |
| A, then B (what worst first prefers) | 60 + 120 = 180 | A: 100 + 60 = 160 |

**Why no service target?** Due times with a lateness penalty need a target and a weight, both chosen by
hand, and if every order is already late the penalty changes nothing. Worst first has no parameter to
tune on the benchmark (D22).

**Testing.**
- The worked example: the evaluator, the oracle, insertion and CP-SAT each choose as in the table.
- On every plan of 20 random problems, ordering by `plan_cost` equals ordering by (longest lead time,
  total).
- CP-SAT equals the brute-force optimum on 150 random problems under worst first.
- Insertion is never better than the optimum, and finds it in 142 of 200 random problems.
- Generated runs deliver every order, reproducibly.

**Result** ([benchmarks.md](benchmarks.md), "worst-first objective").
- **Worst case.** At high load, `cpsat-fair` cut the worst-case completion time against greedy by 80 s
  and 100 s, and had the best P95 of all variants.
- **Mean.** It kept 62–80% of plain CP-SAT's gain in mean completion time.
- **Solving.** Its decisions were also easier to prove optimal: over 99% of searched decisions, against
  about 85%.

---

## 12. Where single-robot shortest paths stop being enough

A shortest path is computed as if the robot were alone in the warehouse. With several robots:

- **Head-on conflict.** Two robots are routed in opposite directions through the same single-lane
  corridor at the same time.
- **Vertex conflict.** Two robots arrive at the same intersection at the same moment.
- **Swap conflict.** Two robots exchange positions along one edge.
- **Congestion.** Every robot picks the same "shortest" aisle, so the planned lengths are no longer the
  real travel times.

Solving these requires adding **time** to the search: space-time A*, reservation tables and prioritized
planning (M4), together with an execution policy that tolerates delays. Shortest paths remain the
building block. They supply the distance estimates for dispatch (M2) and the heuristic for space-time
search.

---

## 13. Traffic (`traffic/`, M4)

### 13.1 Occupancy, conflicts and reservations (D23)

**Problem.** Section 12 lists the ways robots can collide. Before planning around each other, robots
need a precise definition of "being somewhere", and a way to check that no two of them ever are at
once.

**Timed paths.** A `TimedPath` is a sequence of visits (node, arrival tick, departure tick). Between two
visits the robot drives the edge that joins them, in exactly that edge's travel time. Waiting is
simply a longer visit. A path may end in a **hold**: the robot stays at its last node, with no planned
departure.

**Occupancy.**
- At a node, a robot occupies the *closed* interval [arrival, departure].
- On a corridor, the undirected connection {u, v}, it occupies the *open* interval (departure,
  arrival).
- One robot per node and one robot per corridor. A corridor is one resource in both directions, so a
  single rule covers head-on and swap conflicts, and a robot catching up with another.
- With integer ticks every comparison is exact. A node vacated at tick t can be entered at t + 1, but
  a corridor left at tick t can be entered at t.

**The independent checker** (`find_conflicts`). It groups occupancies by resource, sorts them by
start, and compares each with the following ones until one starts after it ends. It reports each
conflict as vertex, head-on or following. It is the reference for the reservation table, the planner
and the simulator, so it shares nothing with them except the definition of an occupancy.

**The reservation table.**
- Per resource, it keeps a list of reservations, sorted by start and never overlapping one another. A
  new reservation can therefore only overlap its neighbours in that order, so checking costs O(log k)
  for k reservations on a resource.
- A path is reserved entirely or not at all. Any overlap with another robot's reservation raises an
  error, so a planning bug shows up at the moment it happens.
- A hold reserves its node until `FOREVER`. `end_hold` cuts it short when the robot's next path takes
  over, and `prune` forgets reservations that have ended.
- **Safe intervals** (`safe_intervals`) are the gaps between a node's reservations: maximal closed
  intervals in which the node is free. These are what the path planner searches over (section 13.2).

**Testing.**
- Hand cases for every conflict kind: head-on, swap, following, a crossing at an intersection, a node
  vacated and entered at the same tick, a hold, and the legal corridor hand-off.
- Safe intervals, holds, pruning, and refusal without side effects, all checked by hand.
- On 300 random scenarios of four random paths each, the table accepts a path exactly when the
  independent checker finds it conflict-free.

### 13.2 Safe-interval path planning for itineraries (`traffic/sipp.py`, D24)

**Problem.** Plan a robot's whole job as one conflict-free timed path. The robot drives from where it
stands to a sequence of stops, each with a service time (loading, unloading), and ends with a hold at
the last stop. It must avoid every reservation already in the table, waiting where needed, and finish
as early as possible.

**Why not search over (node, tick)?** At 0.1 s ticks, an edge takes 10 to 40 ticks, and waits can be
long. A search over (node, tick) states expands every tick of every wait.

**Safe intervals (Phillips and Likhachev, ICRA 2011).** A node's ticks fall into a few *safe
intervals*: maximal stretches in which no other robot is there. The search runs over (stage, node,
safe interval) and keeps the earliest arrival in each. Within an interval, arriving earlier is never
worse, because the robot can wait there. So keeping only the earliest arrival per state loses nothing:
SIPP finds the same earliest itinerary as a search over every tick, with far fewer states.

**One step.** From node u, reached at tick t inside safe interval [s_u, e_u], to a safe interval
[s_v, e_v] of a neighbour v over an edge of τ ticks, depart at the earliest d with:
- d ≥ t and d ≥ s_v − τ;
- the corridor free during (d, d + τ). A single pass over the corridor's sorted reservations pushes d
  past any that overlap;
- d ≤ e_u (the robot waits at u until it leaves) and d + τ ≤ e_v.

**Stops.** At the current stage's stop, the robot may start its service. It stays for the service
time inside the same safe interval, then moves on to the next stage. The last stop is a hold, so the
search can finish only in a safe interval that never ends. That is why another robot's hold on the
goal makes the itinerary impossible, and the planner then returns `None`.

**Heuristic.** A* with the free-flow time to the current stop, plus everything that comes after it:
later services and the free-flow trips between later stops. Free-flow distances come from one
backwards Dijkstra per stop node, cached. Waiting only adds time, so the heuristic never
overestimates. It is a shortest-path distance plus a constant per stage, so it is also consistent.

**Cost.** Proportional to the number of (stage, node, safe interval) states reached, times their
degree, with a log factor for the heap. Measured on the large map, planning 30 whole jobs one after
another (prioritized, each against the earlier ones): 1.4 ms per job on average, at most 3.1 ms,
expanding 156 states on average.

**Testing.**
- By hand:
  - free flow;
  - waiting at the start because an oncoming robot needs the corridor, and then the node beyond it
    (arrival at tick 33 instead of 20);
  - services at a stop, at the start, and at the node of the final hold;
  - a held goal, which gives `None`;
  - malformed requests.
- Against a brute-force search over (stage, node, tick) states on 300 random 3 × 3 grids, with random
  reservations and holds of other robots:
  - 288 itineraries were possible, and SIPP's arrival equals the brute force's in each;
  - in the 12 impossible cases, both agree that there is no itinerary;
  - 126 of the itineraries needed a wait away from any stop;
  - every planned path drives real edges in their exact travel times, and the independent checker
    finds no conflict with the reservations.

### 13.3 The simulator with traffic (`sim/traffic_control.py`, D24)

**What changes with `traffic=True`.**
- **A job is planned whole at assignment.** One SIPP itinerary covers: to the pickup and load, to the
  drop-off and unload, then to a holding spot, held. It is reserved against every robot planned
  before. The robot's phases then end exactly at the planned ticks; waits are part of its drive
  phases.
- **Holding spots.** After unloading, the robot drives to the nearest free endpoint (storage,
  charger or parking, never a station) that no one holds or plans to visit. Pickups of pending
  orders are avoided where possible. For this, the robot state machine gained the phase
  REPOSITIONING, entered after unloading or when a robot moves out of the way.
- **What the policy sees.** An order whose pickup is reserved by a robot that is not idle there is
  hidden until the reservation ends. A wake-up event at that tick lets the policy decide at once.
  Among orders sharing a pickup, only the oldest is offered, so one decision never sends two robots to
  one pickup. An order whose drop-off is held by a robot still driving there to park is hidden until
  that robot arrives, which is itself an event.
- **Moving out of the way.** When a robot is assigned an order whose pickup or drop-off holds another
  idle robot, that robot is first moved to a free holding spot, and the assigned robot is planned
  after it. If the idle robot is assigned too, its own job is planned first. Holding spots avoid the
  pickups and drop-offs of all pending orders.
- **Drop-offs, added after M4 (D24, amended).** Generated orders always end at a station, where no
  robot ever parks, so only pickups needed this rule. Put-aways and moves between storage locations,
  possible since scenario files (D32), end where a robot may be parked: such a run used to stop with a
  planning error. In a stress test of mixed orders, 66 of 108 runs failed before the fix and none of
  360 after it. Retrieval-only runs are unchanged: 72 scaling runs and 56 breakdown runs reproduced
  the committed results exactly.
- **Cycles.** Two idle robots may each be assigned the order on the other's spot. Neither can be
  planned first, because each goal is held by the other. The cycle is broken by postponing one
  assignment. That robot moves out of the way, and its order stays pending for a later decision. The
  count is reported (`deferred_assignments`).
- **Planned availability.** The snapshot tells planning policies when and where each busy robot will
  become idle, as planned (`planned_ready`). The free-flow prediction of section 11.1 would no longer
  be exact.

**Trajectories.** In both modes, the simulator records every robot's movement as one timed path. In
free flow it records the shortest routes at free-flow times; with traffic, the planned itineraries.
The independent checker of section 13.1 can therefore count conflicts in any run.
- In free flow, conflicts are expected, because robots pass through each other.
- With traffic, there must be none.

**Invariants added.**
- Every idle robot holds its own node in the reservation table.
- Every drive phase lasts exactly as planned, and never less than free flow.
- A planning failure, which D24 rules out, raises an error.

**Testing.**
- **Head-on.** On a hand-built map, two robots start crossing the same two-way corridor in opposite
  directions. In free flow the checker finds the head-on conflict. With traffic, the second robot
  waits at its parking spot until tick 21, so it enters the corridor just after the first robot has
  cleared it. Both trajectories match the hand-derived timelines tick for tick.
- **Moving out of the way.** The idle robot on the pickup moves to the nearest free parking spot first,
  and the assigned robot is planned around it. The timelines are exact.
- **Cycle.** Two robots on each other's pickups. One assignment is postponed and that robot moves
  away. Everything is delivered without conflicts.
- **Generated runs.** 36 runs: small and medium maps, 1, 3 and 6 robots, 3 seeds, greedy and
  `insertion-fair`. Each delivers every order with zero conflicts, every trajectory drives real edges
  in exact times, every log entry agrees with the trajectories on where the robot was, and a second run
  is identical.
- **Free flow is unchanged.** The M2 comparison table reproduces exactly.

### 13.4 Results: the cost of congestion

See [benchmarks.md](benchmarks.md), "Traffic". In short:
- **Safety.** With traffic, zero conflicts in 240 runs. In free flow, robots run into each other 61 to
  554 times per run.
- **Moderate load.** Traffic costs 5 to 12 s of mean completion time.
- **High load.** It costs 132 s and 189 s for greedy, but 67 s for `cpsat-fair`.
- **Planning matters more with traffic.** With traffic, `cpsat-fair` beats greedy by 97 s and 160 s
  at high load, against 32 s and 38 s in free flow. Less empty driving means fewer robots in the
  aisles, so fewer waits.

### 13.5 Executing plans when robots are delayed (`traffic/execution.py`, D25, Phase 2)

**Problem.** A plan gives every robot exact arrival and departure ticks. A robot that is slowed down,
for example by an obstacle or a person in the aisle, can no longer keep them. If every robot simply
keeps its own schedule (*naive* execution), a late robot meets others where the plan assumed it had
already left.

**Approach: keep the order, not the times.** A conflict-free plan fixes, for every node and corridor,
the order in which robots use it. *Ordered* execution keeps that order:
- a robot leaves a node no earlier than planned, and not before its service there is done;
- it enters a corridor only after every robot planned earlier on that corridor has left it;
- it enters a node only after every robot planned earlier at that node has left it, at least one tick
  later. Until then it waits at the end of its corridor, still occupying the corridor.

This is the idea behind the temporal plan graph of MAPF-POST (Hönig et al., ICAPS 2016) and the action
dependency graph (Hönig et al., IEEE RA-L 2019).

**Why wait at the end of the corridor?** A plan may send a robot towards a node that another robot
will leave before the first one arrives. Demanding an empty node before departing would add waiting
even when nobody is late. Waiting at the end of the corridor instead means that, without delays, every
event happens exactly at its planned tick.

**Why it cannot deadlock.** Every dependency points from an event planned earlier to one planned later:
- leaving a corridor comes before another robot enters it;
- leaving a node comes before another robot arrives there;
- each robot's own events follow its path, and driving takes at least one tick.

Sorting all events by planned time, with arrivals before departures at equal ticks, is therefore a
topological order. Each event's actual time follows in one pass, as the maximum over its dependencies:
a longest-path computation in a directed acyclic graph. It always finishes, and a delay can only make
events later, never earlier.

**Delay model.** Each time a robot drives an edge, with probability p it takes extra time drawn
uniformly from a range. A draw always uses the same amount of the random stream, so a robot's sequence
of delays doesn't depend on earlier outcomes.

**Testing.**
- **By hand.**
  - A delayed leader: its follower waits for the corridor, then for the node, and arrives at tick 35.
  - A slow service: the follower drives on but waits at the end of its corridor, entering the node at
    tick 31 instead of 13.
- **181 random fleets**, planned one robot after another with SIPP on a grid, each robot with a
  service stop:
  - without delays, both policies reproduce every plan exactly;
  - with random delays (30% of edges, 1 to 15 extra ticks), ordered execution has zero conflicts in
    every fleet. Every event is no earlier than planned, every service is done, and every drive takes
    at least its travel time plus its delay;
  - naive execution of the same fleets causes 136 conflicts, in 71 of them;
  - adding delay never makes any event earlier.

**In the fleet simulator** (`simulate(..., traffic=True, delays=..., execution=...)`):
- **Movement.** With traffic, robots now move edge by edge. One event fires when a robot leaves a
  node, and one when it reaches the end of a corridor; drives end on arrival, not at a planned tick.
- **Plans keep arriving.** Itineraries are planned throughout the run, so the order of robots is kept
  *online*: every node and corridor has a queue of planned uses, sorted by planned time
  (`ResourceOrder`). A robot enters a resource only when its use is first in the queue, and a node
  also needs one tick of clearance. New itineraries are planned from the current tick on, so their
  uses queue behind every use planned earlier, including those of late robots. All waits still point
  forward in planned time.
- **Phases.** A delayed drive extends its phase (`RobotState.postpone`). Loading and unloading keep
  their fixed durations, and a robot leaves a stop no earlier than planned.
- **The policy sees no extra decision points.** Only events that change a robot's phase can lead to a
  dispatch decision, so movement alone never makes the policy decide at a tick it otherwise wouldn't.
- **Testing.**
  - Without delays, 72 runs of the M5 scaling benchmark (3 seeds × 8 configurations × 3 policies,
    `cpsat-fair` at 25 robots included) reproduce the committed results exactly, KPI for KPI.
  - With delays (20% of edges, 5 to 40 extra ticks), 24 generated runs:
    - ordered execution delivers every order with zero conflicts, reproducibly. Its log agrees with
      its trajectories, and no drive is faster than free flow;
    - naive execution causes conflicts in all 24 runs, 776 in total.

**Result** ([benchmarks.md](benchmarks.md), "Delays").
- **Safety.** Ordered execution had zero conflicts in all 240 benchmark runs with delays, where naive
  execution would cause 149 to 531 per run.
- **Cost.** Delays cost capacity, steeply under load. With 20% of edges delayed by 1 to 5 s, greedy's
  mean completion time at moderate load rose from 118 s to 623 s.
- **Planning.** `cpsat-fair` held up best: its lead over greedy grew to 412 s at moderate load, and to
  984 s at high load.

### 13.6 Batteries and charging (`domain/energy.py`, D26, Phase 2)

**Energy model.** Battery levels are integers, as time is (D6): one unit is the energy of driving one
millimetre. Every edge therefore drains a whole number of units, its length in millimetres, and
comparisons are exact.
- A robot standing idle at a charger gains a fixed number of units per tick, up to its capacity.
- The default battery is synthetic, chosen so that charging matters within a one-hour run. It lasts
  one hour of driving (3,600,000 units) and charges fully in 20 minutes (300 units per tick).
- Start levels are seeded, uniform between 30% and 100%.

**Why batteries should not run empty.** Only robots above the low threshold (25% by default)
receive jobs. That threshold must cover the most driving a robot can need before it reaches a charger:
- a job: to the pickup, to the drop-off and to a holding spot;
- then the drive to a charger.

That is four drives between endpoints. Each is at most the longest shortest route between two
endpoints on the map, if robots drive shortest routes. `safe_low_m` computes this bound, and
`EnergyModel` refuses a threshold below it. On the large map the bound is 456 m, 13% of the battery,
so the 25% threshold leaves a margin of 444 m.

**Where the argument is not a proof (found in P2.4).** With traffic, two things can exceed the bound:
- **Detours.** A SIPP itinerary may take a longer route when that arrives earlier than waiting.
- **Extra moves.** A low robot waiting for a charger can still be moved out of the way of a pickup.

Neither is bounded. The invariant stops the run with an error if a battery would go below zero. In the
240 benchmark runs with batteries the lowest level was 18.2%, against 12.3% if every drive were
shortest. A proof would need the energy of planned itineraries checked against the threshold, or low
robots moved only towards a charger (backlog).

**Testing.**
- The unit conversions, drains and charging are checked by hand, including the cap at full charge
  and partial ticks.
- Start levels are seeded and stay within bounds.
- On a hand map the safety bound is exactly 16 m. A battery whose threshold falls below it is refused,
  and one exactly at it is accepted.

**Charging when low, in the simulator (`sim/simulator.py`, P2.2).** Batteries need traffic mode, because
chargers are shared resources and only traffic mode models who occupies a node. Energy is settled
lazily: a level changes when a robot reaches the end of an edge (drain) and when it leaves a charger
(charge since arrival), so no event is needed per tick of charging.

Every decision point (after an event that changes a phase) runs in three steps (four with
opportunistic charging, below):
1. **Charging status.**
   - An idle robot below the low threshold goes out of service.
   - A robot out of service that has charged to the high threshold returns to service.
   - Each low robot that is neither charging nor driving to a charger goes to the nearest free charger,
     by travel time. The drive is an ordinary move (REPOSITIONING), planned with SIPP like any other,
     with a hold at the charger.
2. **The dispatch policy.**
   - Robots out of service are hidden from it (`DispatchSnapshot.unavailable`).
   - Their expected return is in `planned_ready`: the tick they will reach the high threshold, at
     their charger. A look-ahead policy can therefore plan orders for them. When a robot has a
     planned ready time, availability uses it first (`dispatch/horizon.py`).
3. **Claiming occupied chargers.**
   - A charger held by an idle robot in service is claimed only now, after the policy, because the
     policy may have sent that robot to work instead.
   - Otherwise the holder is moved out of the way (the D24 mechanism for pickups), and the low robot
     drives to the charger.
   - If no charger is usable, the low robot stays where it is. It is checked again at every decision
     point, and the wait is measured.

**Wake-ups.**
- A low robot's return to service is scheduled as a wake-up event, so the policy sees it at the exact
  tick it reaches the high threshold.
- A robot that starts the run low leaves for a charger at tick 0.

**When a run ends.** A run ends at its last event that did something. When only wake-ups are left and no
order is waiting, every order has been delivered and no robot is moving, so the run stops.
- Without this rule, charging after the last job would lengthen the run. That would dilute throughput
  and utilization, which are both measured over the run.
- Time out of service is counted until the robot returns to service or the run ends.
- In all 144 runs without batteries checked (72 from the M5 scaling benchmark, 72 from the P1 delay
  configurations), the rule never fired, so earlier results are unchanged.

**Invariant and KPIs.**
- A battery below zero raises `SimulationInvariantError`. The threshold rule (above) is meant to make
  this impossible; the invariant checks it on every edge.
- The result reports charging trips, robot time out of service, robot time waiting for a charger, and
  the lowest level reached, as a share of capacity.

**Cost.** Steps 1 and 3 are O(R + C) per decision point, plus one SIPP plan per robot sent to a charger
or moved off one (R robots, C chargers).

**Testing.**
- Hand map, tick by tick:
  - a robot that ends a job low drives to the charger, charges, and takes the next order at the exact
    tick it reaches the high threshold;
  - a robot that starts low leaves at tick 0 and is not offered to the policy until it is charged;
  - a robot that starts low on a charger returns to service through its wake-up alone;
  - a charged robot on the charger is moved away for a low one;
  - a run with nothing left to do ends when the last robot reaches its charger.
- Generated runs on two maps, with a small battery so robots charge within half an hour: every order is
  delivered, there are zero conflicts, the lowest level stays above zero, and runs are reproducible.
- Without batteries, the 72 runs of the M5 scaling benchmark reproduce the committed results exactly.

**Opportunistic charging (P2.3, D27).** `simulate(..., charging=...)` takes one of three policies:

| Policy | Robots out of service charge to | Idle robots in service top up |
|---|---|---|
| `when-low` (default) | the high threshold | no |
| `opportunistic` | the high threshold | yes |
| `opportunistic-early` | the low threshold | yes |

- **Top-ups.**
  - A top-up is step 4 of a decision point, and happens only when no order is waiting.
  - Idle robots in service below the high threshold, not already on a charger, drive to the nearest
    free charger, lowest level first, until none is free.
  - They stay in service: the policy may send them to work from the charger, and a low robot may
    claim the charger (step 3).
- **Why both opportunistic policies are kept.** The early variant is the first version built. A
  development probe at high load showed it thrashing (D27): a robot back at exactly the low threshold
  can do only about one job before it is low again. Keeping it makes that failure mode measurable.
- **Charging KPIs** (`metrics.compute_charging_kpis`):
  - charging trips and top-ups;
  - the share of robot time out of service;
  - robot time waiting for a charger;
  - charger occupancy (the share of charger time with a robot on it);
  - the lowest level reached.
- **Testing.**
  - On the hand map: a robot tops up after a job and is sent to work from the charger at the exact
    tick the next order is released; the same run without top-ups leaves it waiting where it is.
  - Return to service at the low threshold under the early variant, and at the high threshold under
    the other two, to the tick.
  - The eviction rule, and the generated runs (all orders delivered, zero conflicts, batteries above
    zero, reproducible), under all three policies.

**Limitations.**
- The nearest free charger is chosen greedily, by travel time. There is no queue at chargers and no
  look-ahead on charger occupancy (the CP-SAT charging schedule is in the backlog).
- Thresholds are fixed.
- Top-ups ignore demand: a robot may drive to a charger just before an order arrives near where it
  stood.

### 13.7 Breakdowns and replanning (`traffic/execution.py`, D28, Phase 2)

**Why replanning needs re-timing.** Ordered execution (13.5) is deadlock-free for one reason: every
plan was reserved against every other in one clock, planned time, and every queue follows it, so
every wait points forward in that clock. Late robots make planned times stale.
- **A plan made in real time against stale plans is outside that clock.** It can be queued ahead of
  a robot that is physically ahead of it, and the two can then wait for each other.
- **Example.** Robot X waits in corridor c to enter node m, where robot B has broken down. B is
  replanned in real time and leaves m through c. Now B waits for X to leave c, and X waits for B to
  leave m.

**Re-timing (`retime`).** Compute the earliest schedule of every remaining plan from the current
state, in the planned order, assuming no further delays.
- **Input:** each robot's remaining plan, from the visit it stands on or has just left, and its
  progress there:
  - when it arrived, and, if it is in the corridor after the visit, when it left;
  - the earliest tick of its next event: reaching the end of its corridor (a breakdown makes this
    later), or leaving its visit once any service is done.
- **The computation** is the one pass of `execute_plans`, with the events that already happened
  fixed. Events are taken in planned order:
  - an arrival waits for the robots planned earlier at the node to leave, plus one tick of
    clearance;
  - a departure waits for the robots planned earlier on the corridor to leave it, for any service,
    and for the planned departure.

  Nothing pending happens before now. Resources that robots have finished with only give their
  last leaving tick.
- **Why it is correct.** Every dependency still points forward in the planned order, so one pass in
  that order is a longest-path computation in a directed acyclic graph.
- **What it guarantees.** The result keeps every route and the order at every resource. Successive
  uses of a resource are therefore separated in the new times, and the re-timed plans are mutually
  conflict-free: one clock again, matching reality as of now.
- **Cost:** O(E log E) for E remaining events, dominated by the sort.

**Testing.**
- **Hand cases:**
  - a robot on schedule is unchanged;
  - a breakdown ahead makes the follower wait exactly the repair time, at the corridor entrance;
  - service and the current tick hold a robot standing at a node;
  - a node needs a tick of clearance after the last robot left it.
- **Oracle.** Random SIPP-planned fleets are executed in order, with and without delays, and cut at
  3 random ticks each, over 450 cuts in all. Re-timing from the state at the cut must reproduce,
  visit for visit, the ordered execution in which nothing is delayed after the cut. The re-timed
  plans are checked conflict-free.
- **Mutations.** Removing the node order or the corridor order from `retime` makes the oracle fail.
  Removing the clearance after robots that have left fails a hand case.

**Breakdowns in the simulator (P3.2).** `simulate(..., breakdowns=..., recovery=...)` needs traffic
and ordered execution.
- **When a breakdown happens.** When a robot enters a corridor, it draws a repair time from its own
  seeded stream, with the same model as delays: a share of edge traversals, and a uniform range of
  ticks. A broken robot stays in its corridor for the repair, and holds it in both directions.
- **What is announced.** The repair is announced at once; a delay on the same edge is not. The
  simulator records, for each robot in a corridor, when it is *known* to reach the end: the planned
  travel time plus any repair, without the delay.
- **`wait`.** Nothing else happens. Robots planned after the broken one, on its corridor and beyond,
  wait (13.5). New plans are made against the old times, as if it had left on time.
- **`retime`.** At every breakdown, every moving robot's remaining plan is re-timed from the state
  of the simulation:
  - where it stands or which corridor it is in;
  - when it arrived;
  - any service in progress;
  - when each resource was last left.

  Then the reservation table, the queues and every predicted time are rebuilt from the result:
  phase ends, the planned ready times dispatch sees, and wake-ups for pickups that become free.
  Stale reservations of robots that are now idle are pruned first.
- **Why re-timing moves no robot by itself.** A re-timed departure is never later than when the robot
  could actually leave: each term of it (planned time, arrival plus service, predecessors leaving
  the corridor) is a lower bound on the real one, because delays only add. Only *new* plans, made
  against the re-timed table, differ.
- **Testing.**
  - A ring map with scripted breakdowns: one robot breaks down for 30 s in the top corridor, and a
    second robot is then sent the short way round, through that corridor.
    - Under `wait`, the second robot's plan uses the broken robot's corridor and waits behind it:
      pickup at tick 360.
    - Under `retime`, its plan goes the long way round: pickup at tick 75.
  - With every plan made at tick 0, `retime` reproduces `wait` exactly, trajectories and event log,
    on 6 generated scenarios with breakdowns.
  - Generated runs on two maps under both policies deliver every order with zero conflicts and are
    reproducible. Breakdowns combine with batteries.
  - Without breakdowns, the 72 committed M5 scaling runs reproduce exactly.

**Replanning (`replan`, P3.3).** After re-timing, every robot whose last stop is now later than before
is replanned, in the order of its next event.
- **The anchor.** A robot in a corridor is committed to its end: its new plan starts at the next node,
  at its re-timed arrival there. A robot standing at a node starts there, once any service in
  progress is done.
- **The new plan.**
  - The robot's reservations are released, and SIPP plans again from the anchor through its
    remaining stops (pickup, drop-off, holding spot, with their services), against every other
    robot's current plan.
  - The new plan is kept only if it reaches the remaining stops lexicographically earlier: the next
    stop first. Otherwise the re-timed plan is reserved again.
  - Every kept plan was reserved against all others in the re-timed clock, so the queues still follow
    one clock and execution cannot deadlock (D28).
- **Bookkeeping.**
  - The robot's queued uses are replaced.
  - The job's drives, stop times and the ready time dispatch sees are rebuilt.
  - The current drive gets its new route (`RobotState.reroute`: same start, same destination).
  - A robot standing at a node gets a fresh departure. Its old one is cancelled: departures carry an
    epoch, and a replan moves the robot to a new epoch.
  - Waiting robots that are now first in their queue are woken. A replanned robot may have left the
    queue they were waiting in, and no robot leaving a resource would wake them.
- **Never early, by rule.** No robot reaches or leaves a visit before its current planned time: the
  simulator re-checks at every departure and every node entry, and re-timing uses both as lower
  bounds.
  - **Why it matters.** It guarantees that a robot standing on a resource has a queue key no later
    than when it entered, so no plan made afterwards can be queued ahead of it.
  - **Why it needs enforcing.** Plain ordered execution gets it for free. Replanning does not: a
    re-timed plan can predict a wait behind a robot that is then replanned faster. The robot's
    departure, scheduled before the re-timing, then came early.
  - **How it was found.** A stress test on the large map, up to 25 robots with frequent breakdowns
    and delays, found conflicts, stopped fleets and a re-timing that failed. After both fixes, all 96
    stress runs delivered every order with zero conflicts. 32 of them ran under `replan`, with 79,727
    replans in total.
  - **Cost.** A replanned robot can make the re-timed plans of robots behind it pessimistic. They
    keep a predicted wait that no longer exists, until the next breakdown re-times them.
- **Order matters, as in all prioritized planning.** Robots replan one after another, so the first
  can take a route the second would have liked. Each kept plan is better for its own robot, given the
  plans of everyone else at that moment; the fleet as a whole is not optimized.
- **Cost:** one SIPP search per delayed robot at each breakdown.
- **Testing** (ring map, scripted breakdowns):
  - **A robot planned behind a broken one detours.** R1 breaks down in the top corridor, and R2's
    plan, made before the breakdown, follows it there.
    - Under `wait` and `retime`, R2 waits at A from tick 11 to tick 330, and reaches its pickup at
      tick 360.
    - Under `replan`, its anchor is A (it was in the corridor to A), and it goes A, C, D, B: pickup at
      tick 61.
  - **A robot goes ahead of a broken one.** R2 breaks down in its spur, and R1 was planned to pass B
    after it. Without replanning R1 waits for R2 and reaches the station at tick 352; replanned, it
    passes B first and is there at tick 75.
  - **Generated runs.** On two maps under all three policies, every order is delivered, with zero
    conflicts, reproducibly, and replanning happens in them. Breakdowns combine with batteries under
    `replan`.

---

## 14. Interview notes

**Why does Dijkstra need non-negative edge lengths?**
The proof that a popped node's distance is final assumes edges only add length. With a negative edge, a
route could pass through a far-away node and still come back cheaper, so a "final" distance could later
turn out wrong. Bellman–Ford handles negative lengths, at O(VE) cost.

**Why is Dijkstra insufficient for collision-free multi-robot routing?**
It plans in space only, for one robot. It has no notion of *when* a robot occupies a node or an edge,
so it can't avoid vertex, swap or head-on conflicts. You need a time dimension, either a search over
(node, time) states or reservations, and a policy for which robot yields.

**When is A* no faster than Dijkstra?**
- When the heuristic is weak (close to zero).
- When the target is unreachable: both must exhaust the reachable region.
- On very small graphs, where heuristic overhead dominates.
- When obstacles or one-way constraints force routes far from the straight line.

**Admissible vs consistent?**
- Admissible means the heuristic never overestimates the remaining length.
- Consistent means it never drops by more than an edge's length across that edge.
- Consistent implies admissible, and it lets A* use a closed set without re-expanding nodes.

**Why lazy deletion instead of decrease-key?**
Python's `heapq` has no decrease-key. Pushing duplicate entries and skipping outdated ones keeps the
code simple. The cost is a heap that can grow to O(E) entries, which is negligible on sparse warehouse
graphs.

**Why the insertion counter in heap entries?**
- Determinism: equal distances resolve in a fixed order, so runs are reproducible.
- Safety: Python never has to compare two node ids, or two nodes, to break a tie.

**Why integer ticks rather than float seconds?**
Conflict detection is equality and overlap testing on times. Integers make those tests exact, and they
make a reservation table a plain lookup by (node, tick). Floats would need tolerances in every
comparison, and a wrong tolerance shows up as a phantom collision or a missed one.

**Why round travel times up rather than to the nearest tick?**
Rounding to the nearest tick can make a plan assume a robot arrives at, or leaves, a node before it
physically does. A reservation that ends too early is how a collision gets into a "conflict-free" plan.
Rounding up costs at most one tick of slack per edge.

**Why an explicit state machine with immutable states?**
- The table of allowed transitions is small enough to test exhaustively, and it is the specification of
  what a robot can do.
- Immutable states turn "who changed this robot?" bugs into impossible ones: a planner receives a
  snapshot, and only the simulator replaces states.
- Storing the end tick of every phase makes timing errors detectable at the exact event where they
  occur, instead of showing up later as implausible KPIs.

**Why a discrete-event simulator rather than a time-stepped loop?**
Between events nothing changes that the model cares about, so stepping through every tick does work
for nothing. Event-driven simulation scales with the number of events. Time-stepped simulation is the
better fit when continuous interactions are modelled, for example physics or detailed collision
avoidance.

**How do you know the simulator is right?**
- Invariants are checked after every tick, plus the state machine's checks at every transition.
- Timelines on tiny scenarios are checked tick by tick against hand calculations.
- On generated runs, durations are checked against times computed independently.
- The invariant checks are themselves tested, by breaking them on purpose.

**When does optimal assignment beat greedy, and when not?**
Only when a decision has several robots *and* several orders. With one robot or one order, both pick
the same pair. Event-driven dispatch mostly creates exactly those one-sided decisions, so batching into
decision rounds is what gives the optimiser room. The price is waiting time. Measured in M2, that
price was higher than the gain (benchmarks.md).

**Why not CP-SAT for this?**
If each robot takes one order per decision and the cost is a sum over pairs, the problem is exactly
the linear assignment problem, and an O(n³) algorithm solves it optimally. CP-SAT earns its place
when the model needs more: sequences of several orders per robot, time windows, charger capacity or
battery limits (M3).

**Your optimizer didn't beat greedy. Isn't that a failure?**
It is a measured result, and it shows why. The decisions an event-driven dispatcher faces are almost
always one robot or one order, and there the greedy choice is optimal. Batching created room for the
optimizer, but the waiting it adds cost more than better matching saved. The useful conclusion is
where optimization *can* pay off: look-ahead, multi-order sequencing and time windows. That is what
the CP-SAT phase targets. Reporting this honestly is worth more than a tuned demo.

**What is a receding horizon, and why commit only the first step?**
Plan several steps ahead, carry out only the first one, then plan again from the new state. The later
steps exist to make the first one good. For example, a robot should not take a far order if a busy
robot will be next to it soon. They are not commitments, because new orders arrive and invalidate
them. This is the same idea as model predictive control.

**Why plan busy robots at all?**
In M2 every decision saw only idle robots, so "the nearest robot" meant "the nearest idle robot". A
robot about to deliver next to the pickup is often the better choice. Predicting when and where each
busy robot finishes turns that into a number the planner can compare.

**How do you know the CP-SAT model is right?**
- On 300 small problems it matches a brute-force search over every plan.
- Its plans are re-evaluated by an independent evaluator: the solver's numbers are never trusted on
  their own.
- Every solve also checks bound ≤ cost ≤ the insertion plan's cost.
- The evaluator itself is checked against the simulator: committed jobs are delivered at exactly the
  planned tick.

**Why does CP-SAT spend most of its time proving rather than finding?**
The objective is a sum of completion times, linked by constraints that apply only if an arc is chosen.
The linear relaxation of such constraints is weak, so the lower bound rises slowly even when the best
plan is already known. In practice: use the hint, stop at a limit, and report the remaining gap.

**Why a deterministic time limit?**
A simulation run calls the solver hundreds of times, and each plan changes what happens next. With a
wall-clock limit, a busy machine would get different plans and a different run. CP-SAT's deterministic
time counts work done, so the same input always gives the same plan.

**Why a giant tour instead of one circuit per robot?**
It needs about (R + n)² literals instead of R · n², because the trips between orders don't depend on
the robot. Its catch is symmetry: the robots' segments could appear in any order around the tour.
Pinning each order's owner fixes that, so each plan has one representation. Measured on recorded
decisions, it was faster than the per-robot model.

**Did CP-SAT earn its place?**
Measured against a heuristic on the *same* model, not just against FIFO greedy: yes, at high load,
where it beat cheapest insertion by about 11 s of mean completion time, with intervals that exclude
zero. Most of the gain over greedy, however, comes from the planning model itself: look-ahead and
sequencing. Insertion already gets 36–39 s of the 47–51 s. And optimising the mean made the worst case
worse. The honest summary is "the model matters most, exact search adds a measurable increment, and
the objective needs a fairness term". With the worst-first term (section 11.5), CP-SAT still beat
insertion at high load, by 5–9 s.

**How did you fix the starvation, and what did it cost?**
I changed the objective, not the solver. Plans now minimise the longest lead time first, then the
total. The weight that makes this lexicographic follows from an upper bound on completion times, so
there is nothing to tune. At high load, the worst case dropped by 80–100 s against greedy, and P95 was
the best of all variants. About a fifth to two fifths of the mean gain was given up. A useful side
effect: the solver proved optimality far more often, because the dominant term has a tight bound.

**What does SIPP buy over space-time A\*?**
The same optimal earliest-arrival result, from far fewer states. Space-time A* searches over (node,
tick), so a 10 s wait at 0.1 s ticks costs 100 expansions. SIPP searches over (node, safe interval),
so the same wait is one transition. This is correct because arriving earlier within a safe interval
is never worse when the robot may wait. On this project's maps that brings a whole-job plan down to a
couple of milliseconds.

**Why can prioritized planning get stuck, and why doesn't it here?**
In general, a robot planned early can block one planned later, for example by parking on its only
route or its goal, and prioritized planning has no way to undo that. Here, robots stop only at
dead-end endpoints, and goals are endpoints that no one else holds or plans to visit. So no hold
blocks a route or a goal, and every other reservation eventually ends. A plan therefore always
exists. This is the well-formed infrastructure argument of Čáp et al. (ICAPS 2015).

**What did adding traffic change in your conclusions?**
Two things.
- **Free-flow results were optimistic.** At high load, mean completion grew by about 40% (37% and 46%)
  for the best planner, and up to double for greedy.
- **The value of planning was understated.** In free flow, the best planner beat greedy by 38 s at 10
  robots and 300 orders/h; with traffic, by 160 s.

Less empty driving matters far more when every extra trip occupies aisles that others need. That is
why the portfolio benchmarks run with traffic.

**How do you know the traffic simulation is collision-free?**
- Every run records each robot's full trajectory.
- An independent checker, which shares nothing with the planner except the definition of
  occupancy, compares every pair of robots on every node and corridor.
- It found zero conflicts in all 276 runs with traffic: 36 test runs and 240 benchmark runs.
- The same checker finds hundreds of conflicts per run in free flow, so it is not trivially silent.

**Why prioritized planning rather than a fleet-wide optimum?**
It is fast (milliseconds per job here) and, on well-formed layouts, never fails. Its weakness is
first come, first served: a robot planned early can force long waits on later ones. Conflict-based
search or priority-swapping schemes can do better, at a much higher cost per decision. They are in
the Phase 2 backlog, to be compared against this baseline.

**How does it scale with the fleet?**
- **Dispatch quality.** At high load with traffic, the best planner's lead over greedy grows with the
  fleet: 91 s at 3 robots, 160 s at 10, 265 s at 25. More robots share the aisles, so greedy's
  unnecessary empty trips delay everyone.
- **Computation.**
  - Greedy and insertion decide in a few milliseconds at every size.
  - CP-SAT's decisions grow with the fleet and the queue, to a P95 of 265 ms at 25 robots, bounded per
    solve by its deterministic limit.
  - Path planning costs a couple of milliseconds per job, because SIPP searches safe intervals rather
    than ticks.
- **Untested.** Beyond 25 robots, other layouts and clustered demand (LIMITATIONS.md).

**What happens to a conflict-free plan when a robot is late?**
If every robot keeps its own times, conflicts appear: in 71 of 181 random test fleets, with 30% of
edges delayed. The fix is to keep the *order* in which robots use each node and corridor, not the
times. A robot waits for everyone planned before it on its next corridor and node, if necessary at the
end of its corridor. All these waits point forward in planned time, so they form a directed acyclic
graph: execution cannot deadlock, and without delays it reproduces the plan exactly.

**When should a robot charge, and when should it go back to work?**
Two thresholds give hysteresis: go to charge below the low one, return to service at the high one.
Topping up idle robots when nothing is waiting helped at moderate load (26 s off the mean, 70 to 75 s
off the worst case) and was neutral at high load. Returning robots to work as soon as they pass the low
threshold looked better still at moderate load, but at high load each robot went straight back to a
charger after every job: about 200 charging trips per run instead of 11 to 13, and a worst case 520 to
600 s worse. The mean completion time did not show it, which is why the benchmarks report the worst
case too.

**How do you replan robots that are already executing plans, without risking deadlock?**
Ordered execution is deadlock-free because every plan was made against every other in one clock, and
every queue follows that clock. Late robots make that clock stale, so replanning in real time can put
a robot ahead of one that is physically in front of it. So the fleet re-times first:
- it computes the earliest schedule of every remaining plan from the current state, keeping the order
  (a longest path in a directed acyclic graph);
- it rebuilds the reservations from that;
- then it replans the delayed robots with SIPP against it.

Two things a stress test taught me:
- robots must never run ahead of their current plan, which plain execution guaranteed for free;
- a robot that leaves a queue must wake whoever is now first.

Re-timing alone recovered 43% to 86% of the cost of breakdowns, and replanning 75% to 96%.

**Why paired seeds and confidence intervals?**
Different order streams vary far more than policies do. Running every policy on the same streams and
comparing per-seed differences removes that variation. The interval shows whether the remaining
difference is distinguishable from zero at the sample size used.

---

## 15. The HTTP service (`api/`, D29, Phase 2)

**Problem.** Offer the engine to other programs: one dispatch decision per request, without letting
CPU-bound planning stall the server, and without letting the web layer leak into the core.

**Approach.**
- **Pydantic only at the boundary.** Request and response models live in `api/models.py`.
  `api/work.py` converts a request to the engine's frozen dataclasses, calls the engine, and returns a
  response model. The core never imports `api`.
- **A busy robot is "free at node n from tick t".** In the snapshot, it is idle at n but unavailable,
  with `planned_ready[robot] = (t, n)`. This is exactly how look-ahead planners already see a robot
  out of service until a known tick (D18, D26).
  - Greedy and Hungarian therefore ignore busy robots.
  - Insertion and CP-SAT can plan orders for them and start nothing now. In the test, an order next
    to a robot that is busy for 10 s waits for it, instead of going to an idle robot far away.
- **No CPU-bound work on the event loop.** The endpoints are async and await a process pool, which
  the app creates when it starts and shuts down when it stops. Python threads would contend for the
  interpreter lock; processes do not.
- **Errors.** Validation errors are 422s with their reason: unknown map, policy or node, duplicate
  ids, a busy robot ready before the snapshot's tick, an order not yet released, too many robots or
  orders, unknown fields. Engine errors such as an order with the same pickup and drop-off are
  converted to 422s as well. Nothing returns a stack trace.

**Testing.**
- For every registered policy and 4 generated snapshots with idle and busy robots, the endpoint's
  assignments equal the policy's own decision on a snapshot built independently of the API code.
- Busy robots never start a job. A look-ahead policy can leave an order for a busy robot.
- Invalid requests get 422 with the expected reason.
- One test runs through the real process pool, so requests and results do cross process boundaries.

**Simulation jobs (P4.2).**
- **The protocol.** `POST /simulations` validates a run (the same options as `amr-sim`, each seeded by
  one seed) and answers 202 at once with a job id. The run goes to the process pool.
  `GET /simulations/{id}` reports the job's status, read from its future: queued, running, done with
  the KPIs, or failed with the error's type and message.
- **Bounds.** At most 8 jobs are queued or running; one more is refused with 429. Only the last 100
  finished jobs are kept. A run has at most 50 robots, 1,000 orders per hour and 2 hours of orders.
- **JSON has no NaN.** A KPI that is undefined, such as the completion time of a run that delivered
  nothing, is returned as null.
- **Testing.**
  - A job with traffic, delays, batteries, breakdowns and replanning reports exactly the KPIs, counts
    of breakdowns and replans, and charging trips of the same run made directly.
  - With stand-in executors whose futures never run: a full queue answers 429, and a failing job
    reports its error and no result.
  - Undefined KPIs come back as null.
  - Invalid requests get 422 with their reason.
  - One job runs through the real process pool.

**The container (P4.3).**
- **The image.** Python 3.13 slim, with only `pyproject.toml`, the readme, the license and `src/`
  copied in; it installs the package with the `api` extra and runs as an unprivileged user.
- **Listening.** Inside the container the server listens on every interface, because a published port
  must reach it. The documented `docker run` publishes it on 127.0.0.1 only.
- **Spawned workers.** Worker processes are spawned, not forked, on every platform. Forking a process
  that already runs threads, as a server does, is unsafe; and spawning behaves on Linux as on Windows.
- **Verification.** CI builds the image on Linux, checks that it runs as an unprivileged user, starts
  it, and checks `/health` and a CP-SAT decision from `/plan`. Locally, the same non-editable install
  in a fresh environment served both requests through spawned workers.

---

## 16. A VDA 5050-shaped interface (`vda5050/`, D30, Phase 2)

**Problem.** Real fleets talk to their robots through an interface such as VDA 5050: fleet control
sends *orders*, and robots report their *state*. Show how the engine's plans map onto it, and check
every message against the specification itself.

**The specification, read at the source.** The latest release is version 3.0.0, from March 2026,
published under the MIT license with JSON schemas. Two of its rules shape everything here:
- **An order alternates nodes and edges.** The first node has sequence id 0, the first edge 1, and
  so on. The edge with sequence id n connects nodes n − 1 and n + 1; since 3.0.0, edges no longer
  name their nodes.
- **Fleet control releases a prefix of the order: the base.** The robot drives the base and stops at
  its last node, the decision point. An order update extends the base. It starts at the previous
  base's last node, the stitching node, with the next order update id.

**Messages (P5.1).**
- Order, state, instant actions and connection are plain frozen dataclasses with `to_json`, and
  `from_json` for the messages that are read.
- Topics follow `interfaceName/majorVersion/manufacturer/serialNumber/topic`. Header ids count per
  topic, and timestamps are ISO 8601 UTC with milliseconds.
- **The oracle.** The tests validate every message against the official schemas, vendored with their
  license. They also reject any field a schema does not define, because the schemas themselves allow
  unknown fields.

**Orders from plans (P5.2).**
- **`route_of`** turns an engine itinerary into the route: one node per visit, with its map position
  and its actions (`pick` at the pickup, `drop` at the drop-off), and one edge per drive, with its
  length.
- **`order_message`** releases the route up to a given node: the first order from node 0, an update
  from the stitching node.
- **`order_violations` and `update_violations`** check the specification's rules:
  - one fewer edge than nodes;
  - continuous sequence ids, with each edge between its nodes;
  - a released first node, and nothing released after something unreleased;
  - an edge released only with both its nodes;
  - an update that continues the same order, from the stitching node, with a higher update id.

**Testing.**
- **Hand route:** exact ids, sequence ids, positions, lengths, actions and released flags.
- **Broken rules:** each rule is broken on purpose, and the break is detected.
- **Generated jobs:** SIPP-planned jobs on the medium map become orders and chains of updates with
  random base lengths. Every message follows the rules and validates against the schema.

**Mock robots and fleet control (P5.3).**
- **The bus.** JSON text on the specification's topics, kept in a log. Messages wait on their topic
  until taken, as on a broker.
- **A mock robot.**
  - It accepts a new order only where it stands, and an update only if it passes the update rules;
    anything else is refused with an error.
  - It drives only its released base, at constant speed, and stops at the decision point.
  - It runs its node's actions one after another for their handling time (`pick` for the loading
    time, `drop` for the unloading time).
  - It publishes its state on every event (an order received, a departure, an arrival, an action that
    changes), and at least every 30 s.
- **Fleet control.**
  - It sends each job as an order, and adds the job's itinerary to the planned order of robots (D25).
  - It extends a robot's base, with an order update, by the next edge and its end node, whenever the
    robot is first in the planned order of both.
  - It knows the robots only from their state messages. A state gives the last node reached and
    whether the robot is driving again, and from that fleet control marks which nodes and corridors
    the robot has left.
- **Progress, corrected (D31).** This section first claimed that fleet control cannot deadlock,
  because every wait is for a robot planned earlier. That was wrong.
  - Waiting at the node before a corridor, not at its end, adds dependencies, and they can form a
    cycle.
  - An external review built one: four robots on a one-way ring of four nodes, each standing on a
    ring node and waiting for the node the next robot stands on. Ordered execution finishes it at
    tick 30; fleet control never moved.
  - **Now:** when every robot of such a cycle is ready to leave, first on its next corridor, and next
    after its next node's occupant, all are released one step together, and they move as a train.
  - **Anything else** that stops all progress for 10 simulated minutes raises `StalledError`. No
    general progress proof is claimed.
- **A bug the generated runs found.** A route can use a resource twice: into a pickup's dead-end spur
  and back out through the same intersection, which another robot may pass in between.
  - While the robot's first use of that intersection was released but not yet left, the robot itself
    headed its queue. So fleet control also released the way back, before the other robot had
    passed.
  - Now a later use of a resource is released only once the earlier one has been left. A run that
    used to fail is kept as a regression test.
- **Two more problems the full demo found** (25 robots on the large map):
  - **Jobs sent together.** Fleet control released each job's base as soon as the job was added. A
    robot could then be released through a node at which a job added after it, at the same tick, was
    planned earlier. Now every job of a call joins the planned order before any base is released. A
    job sent later is planned after the existing ones, so it cannot come first.
  - **Parking on a pickup.** `first_jobs` planned every job without avoiding the other jobs' pickups
    as holding spots. One robot parked on another job's pickup, which then had no plan. As in the
    simulator, holding spots now avoid the pickups of the jobs being sent.

  Both are covered by tests that fail without the fix.

**Testing.**
- **A hand case, tick for tick.** A robot stops at its decision point until the robot ahead has left
  the next node. Its order update goes out at the tick that robot reports leaving.
- **Refused messages.** The mock robot refuses an order that does not start where it stands, and an
  update that does not start at the stitching node.
- **Generated runs on the medium map**, 8 robots with one SIPP-planned job each, 4 seeds:
  - every job is reported delivered;
  - the robots' trajectories have zero conflicts;
  - every message in the log validates against the official schemas;
  - every order and update fleet control sent follows the rules;
  - every job is delivered at least one tick later than planned (the latency of the first message);
  - runs are reproducible.


## 17. Replay traces (`replay/`, D33)

**Problem.** A viewer has to show what a run did: where every robot was at every moment, what it
was doing, and what the run achieved. The simulator already knows all of it, but the service
returned only KPIs. The risk in building a viewer is a second, slightly different simulation in
the browser. The trace removes that risk: it carries the run, and the viewer only reads it.

**What is stored.** A robot's movement is a timed path (section 13): visits
`(node, arrive, depart)`. Between two visits the robot drives one edge at constant speed (D12), so
its position at a time `t` between a departure `d` from node `u` and the next arrival `a` at node
`v` is

```text
p(t) = p(u) + (t - d) / (a - d) * (p(v) - p(u)),    for d < t < a
```

At every other time it stands on a node. Visits are therefore a **lossless** record of movement:
the trace stores them, never one position per frame. A viewer that draws 60 frames per second
computes each position from two visits.

**Finding the two visits.** `position_at` (the reference, `viz/motion.py`) scans the visits in
order, which is O(n) per lookup for n visits. A viewer should binary-search the arrival ticks
instead: O(log n) per robot per frame, and O(1) amortized while playing forward, by remembering
the last index. Both must give the same answer as the reference; the golden fixtures check that.

**What a robot is doing** is read from the event log, not from the path. The last `assigned`,
`loaded` or `delivered` event of the robot at or before tick `t` decides: to pickup, loaded, or
free. An event at tick `t` already counts at tick `t`.

**Why a hold is `null`.** Inside the engine a robot that stays where it is has the departure tick
`FOREVER`, which is 2^62. JavaScript numbers are exact only up to 2^53, so that value would not
even survive parsing in a browser. In a trace it is `null`. Runs that finish have no hold: the
simulator ends every trajectory at the last tick of the run.

**Determinism.** The trace contains no wall-clock value and its keys have a fixed order, so the
same run gives the same JSON, byte for byte. A test found one way this could break: the event log
holds a distance of `0` as a whole number in some code paths and as `0.0` in others. Both mean
the same, but they print differently, so a parsed trace would not print as it was read. Every real
number is now written as a float.

**Validation in two layers.**
- *Structure:* a hand-written strict parser, as for maps and scenario files, rejects missing,
  unknown and mistyped fields with their location.
- *Consistency:* the trace is then checked as a whole. Every visit is on the map, every step drives
  an existing edge, events are in time order within the run, only a release has no robot, and the
  outcome lists every order exactly once.

A JSON Schema describes the structure for other languages and for the API documentation. The tests
validate real traces against it with `jsonschema`, which shares no code with the parser, and check
on 31 deliberately broken traces that both reject what they should.

One thing is deliberately **not** checked: that a step takes exactly the edge's travel time. With
delays and breakdowns a step takes longer, and that is the point of recording it.

**Conflicts** come from the independent checker of section 13.2, run on the recorded trajectories.
The trace lists the earliest 1,000 and always gives the exact total. A free-flow run of 50 robots
for two hours has 15,078.

**Through the service.**
- The worker process builds the trace and returns it as gzip bytes. A `SimulationResult` cannot
  be pickled, and megabytes of JSON must not be parsed on the event loop (section 15).
- The server keeps the bytes and sends them unchanged to a client that accepts gzip. Otherwise it
  decompresses them in a thread.
- Storage is bounded by count (20 traces) and by total compressed size (32 MB), oldest dropped
  first. Both bounds are needed: the count bounds bookkeeping, the size bounds memory whatever
  the traces contain.
- A finished job is *settled*: its outcome and trace are moved out of its future, and the future
  is dropped. A `Future` keeps its result for as long as it lives, so 100 kept jobs would
  otherwise hold 100 traces outside the store's limits.

**Measured** (greedy, seed 0, large map, on the development laptop):

| Run | JSON | gzip | Simulation | Building the trace | Writing and compressing |
|---|---:|---:|---:|---:|---:|
| 10 robots, 1 h, traffic | 0.54 MB | 0.11 MB | 0.5 s | 0.08 s | 0.12 s |
| 25 robots, 1 h, traffic | 1.48 MB | 0.33 MB | 2.1 s | 0.45 s | 0.24 s |
| 50 robots, 2 h, traffic (the API's limits) | 3.38 MB | 0.73 MB | 7.0 s | 1.41 s | 0.77 s |
| 50 robots, 2 h, free flow | 3.20 MB | 0.65 MB | 1.3 s | 1.21 s | 0.50 s |

Building a trace includes the conflict check and the consistency check. It costs up to about as
much as a free-flow simulation and a fifth of one with traffic, which is why it is opt-in.

**Golden fixtures.** `scripts/make_replay_fixtures.py` writes three real runs into
`tests/data/replay/`: free flow (with conflicts), a run cut short in the middle of its work, and
traffic with delays, charging, breakdowns and replanning. Each has a trace and a file of samples:
every robot's position and activity from the reference functions, at every arrival and departure,
a third of the way along every edge (a fraction of a tick), around every change of activity, at
tick 0, at the end and after it. A viewer in any language must reproduce the samples from the
trace alone, and a Python test fails if the committed files are not what the script writes now.

**Limitations.**
- **Three activities only.** The event log has the six steps of an order's life. Charging,
  breakdowns and repairs have totals but no events, so a viewer cannot show when they happened.
- **No battery levels over time.** Only the lowest level of the run is recorded.
- **A cut-short run knows the future.** A free-flow drive is recorded when it starts, so a run
  stopped by `max_tick` has visits after its last tick. The service never cuts a run short.
- **Compressed bytes can differ between machines.** The JSON is always identical; the gzip bytes
  depend on the zlib build.
- **Whole traces only.** There is no paging and no streaming, because the largest trace the
  service can produce is 0.7 MB compressed.

**Interview questions.**
- *Why not send positions per frame?* The size would grow with the frame rate and the playback
  speed would be fixed by the server. Visits are exact, small, and independent of both.
- *How do you know the browser shows what the simulator computed?* The browser's interpolation is
  tested against samples produced by the Python reference on real runs, including fractional
  ticks, and the fixtures cannot go stale without a test failing.
- *Why two limits on the store?* A count alone does not bound memory, and a size alone lets many
  tiny entries grow the bookkeeping. Each limit covers the other's blind spot.
- *Why is the trace opt-in?* Existing clients keep identical answers and cost, and building a trace
  is not free.


## 18. The viewer: playing a trace back (`web/`, D34)

**Problem.** Show a recorded run in a browser, smoothly and at any speed, without the browser
ever deciding anything about the run. The trace (section 17) is the input; the viewer's only
freedom is *which moment* it shows.

**One number of state.** Playback is a clock (`web/src/lib/playback.ts`) holding the playhead `t`
in ticks. Every frame, while playing, it advances by

```text
t  <-  t + (real seconds elapsed) * speed / tick_s
```

where `speed` is simulated seconds per real second. The playhead is a real number, so a robot
moves smoothly even when one frame covers a fraction of a tick. Everything on screen is a pure
function of the trace and `t`: scrubbing, stepping and playing backwards in time need no special
cases, because nothing is accumulated from frame to frame.

**The playhead never leaves the run.** `t` is clamped to `0 <= t <= end_tick` in every operation
that changes it. That matters for a run that was cut short: its trace can hold visits after
`end_tick` (section 17), and an unclamped playhead would play a future the run never reached. A
test checks this on the `cut_short` fixture.

**Position** is `poseAt` (`web/src/lib/motion.ts`), the port of `position_at`:
1. Find the last visit `i` with `arrive[i] <= t`, by binary search over the arrival ticks. They
   increase strictly, so this is the visit the Python scan stops at.
2. If `t <= depart[i]`, or `i` is the last visit, the robot stands on node `i`.
3. Otherwise it is on the edge to node `i + 1`, at the share `(t - depart[i]) / (arrive[i+1] -
   depart[i])` of the way.

The same floating-point operations are applied in the same order as in Python, so the tests
compare with exact equality, not with a tolerance: 2,211 sampled positions over three real runs,
at arrivals, departures, a third of the way along every edge, around every change of activity
and after the end. Two mutations of the code (a `<` for a `<=` at a departure, an off-by-one in
the activity lookup) each make tests fail.

**Activity** is the robot's last `assigned`, `loaded` or `delivered` event at or before `t`, by
the same binary search. An order's state is derived the same way from its own six logged steps.

**Cost per frame.** With R robots and at most n visits each, a frame costs O(R log n) lookups plus
drawing the map: O(V + E) for V nodes and E edges. Nothing is precomputed per frame and nothing
grows with the length of the run.

**Measured** in Chrome on the development laptop, on the largest run the service accepts (large
map, 50 robots, 995 orders, a 3-hour run with 621 breakdowns): the trace loaded and was checked in
0.4 s, and playing at 60x, frames took 10 ms at the median and 20 ms at the 95th percentile. That
is why the map is a plain 2D canvas (D34): a rendering engine would add a dependency and solve no
measured problem.

**Two refresh rates.** The canvas reads the playhead on every frame. The panels of text read it
5 to 10 times a second through a throttled subscription: re-rendering lists of a thousand orders
60 times a second would cost far more than the canvas. A change made while paused (a seek, a step)
shows at once.

**What is drawn, and what is not.**
- The map is drawn as the graph it is: points, and lines between them. A one-way connection has
  an arrowhead. No aisle width, robot footprint or clearance is drawn, because the model has none.
- A robot's colour and inner mark show one of the three activities the event log supports. Its
  pointer shows the direction of the edge it is driving.
- The selected robot's path is drawn from the trace: the last minute behind it, and ahead of it
  the recorded path to the end of its current job. "Ahead" is not a prediction: the run is over,
  and the path is what the robot then did.
- Conflicts are those listed by the engine's checker (section 13.2), marked where and when they
  happened. The viewer never infers a conflict from two drawings overlapping.

**The form asks the service what it accepts.** The limits of a run (robots 1 to 50, at most
7,200 s, the charging and recovery choices) are read from the service's OpenAPI document, not
repeated in the viewer. A Python test pins the shape the viewer reads. The service still validates
every request and has the last word; its refusal reasons are shown as they are.

**Limitations.**
- **A viewer of finished runs.** It is not a live view of a running simulation, and it controls
  nothing.
- **No charging, breakdown or battery display.** Their totals are shown as totals. When each
  happened is not in the trace (section 17).
- **Component behaviour is tested by hand in a browser,** not by automated interface tests. The
  logic underneath (trace reading, interpolation, clock, form, view maths) has 116 unit tests.
- **Performance was measured on one machine and one browser.**

**Interview questions.**
- *How do you scrub backwards?* The same way as forwards: the frame is a function of the trace and
  the playhead, with no state carried between frames.
- *Why binary search when the reference scans?* The reference is written to be obviously right;
  the viewer runs it 50 times a frame. The tests show they agree, including against a scan written
  the reference's way.
- *Why exact equality in a floating-point test?* Because both sides do the same IEEE operations
  in the same order. A tolerance would hide a real difference, such as a different formula.
- *What stops the interface from showing made-up data?* It has no source of data except the trace
  and the job result, and the trace is rejected unless it passes the same checks as in Python.


## 19. Robot events and battery readings (`sim/log.py`, `replay/state.py`, D35)

**Problem.** The viewer of section 18 could show three activities, because the order log has
nothing else. Charging, time out of service, breakdowns and battery levels were totals of a run.
To show them truthfully, the simulator has to record when they happen, without changing what
happens.

**Observations.** A robot event is one line appended where the simulator already changes the state
the event describes. It draws no random number, schedules nothing and is never read back. Three
checks show that a run is unchanged:
- every test that existed before passes;
- with the additions removed, the new traces of the three fixture runs equal the committed
  version 1 files byte for byte;
- 156 runs of the published scaling and breakdown benchmarks were made again and their 3,300 KPI
  values compared with the committed results: none differs
  (`scripts/check_published_results.py`).

**The events, and exactly when each is recorded.**

| Event | Recorded at the tick when | Fields |
|---|---|---|
| `charging_started` | the robot becomes idle on a charger (or starts the run on one) | level |
| `charging_stopped` | it leaves the charger | level it charged to |
| `out_of_service` | a decision finds it idle below the low threshold | level |
| `back_in_service` | a decision finds it charged to the return level | level |
| `charger_wait_started` | it is out of service and no charger is free | |
| `charger_wait_ended` | a charger is found for it | |
| `charger_trip_started` | it is sent to a charger | charger, level, reason (low or top-up) |
| `delay` | it enters a corridor and a delay is drawn | edge, extra ticks |
| `breakdown` | it enters a corridor and a repair is drawn | edge, repair ticks |
| `corridor_wait_started` | it reaches the end of a corridor and may not enter the node | edge |
| `corridor_wait_ended` | it enters the node | edge |
| `replanned` | a new route is adopted after a breakdown | |
| `moved_out_of_the_way` | an idle robot is sent to another holding spot | target |

**Intervals.** Four pairs of events open and close an interval. It includes its first tick and not
its last, and one still open at the end of the run ends at the run's last tick.

**Delay against breakdown.** Both are extra time on one corridor traversal, drawn at the entry.

| | Delay | Breakdown |
|---|---|---|
| Cause in the model | an unannounced slow-down (D25) | an announced stop for a repair (D28) |
| Random stream | the robot's delay stream | the robot's breakdown stream |
| Known to the fleet | no: plans assume the robot is on time | yes: recovery re-times and replans |
| Counter in the result | none | `breakdowns`, `breakdown_ticks` |
| On the same traversal | both can occur; each has its own event with its own ticks | |

There is no double counting, because each cause has at most one event per traversal and carries
only its own time. The tests check the identity that makes this precise, for every traversal of
every generated run:

```text
arrive - depart = edge travel time + delay + repair + wait at the end of the corridor
```

and that the wait begins exactly when the first three terms have passed.

**Reconciliation with the simulator's counters.** The counters keep their definitions; the events
must add up to them.

| Counter | Equals |
|---|---|
| `charger_ticks` | the summed length of charging intervals |
| `out_of_service_ticks` | the summed length of out-of-service intervals |
| `charger_wait_ticks` | the summed length of charger-wait intervals |
| `charging_trips`, `top_up_trips` | the number of charger trips with reason low, and with reason top-up |
| `breakdowns`, `breakdown_ticks` | the number of breakdown events, and their summed ticks |
| `replans`, `moves_out_of_the_way` | the number of events of that kind |
| `lowest_energy_share` | the lowest recorded level, as a share of the capacity |

The three interval counters do not overlap in what they count. A robot waiting for a charger is
also out of service, and a robot charging when low is both charging and out of service: those are
different counters of different things, each matched by its own kind of interval, never added to
each other.

**Battery levels: what is a reading.** The simulator charges an edge's energy on arrival, and
settles charging when a robot leaves a charger. A trace therefore has:
- **readings:** one level per visit, on arrival, and the levels in events. These are values the
  simulator computed at that tick;
- **nothing in between while driving.** `level_at` returns the last reading and the tick it dates
  from. It does not spread an edge's energy along the edge: the model does not, so a viewer may not;
- **the charging formula on a charger:** `min(capacity, level + ticks * rate)`, with the numbers
  from the trace. The result is labelled `charging_formula`. It is the simulator's own rule, and the
  energy oracle in the tests confirms that the next reading agrees with it, but it is a calculation
  and is shown as one.

**The energy oracle.** Every level is recomputed from the previous one: charge by the formula if the
robot stood on a charger, then subtract the edge's length in energy units. The recorded levels must
equal the recomputed ones. This is independent of the simulator's bookkeeping.

**Position at the end of a corridor.** Under ordered execution (section 13.5) a robot that reaches
the end of its corridor before its turn waits there. `replay.state.position_at` moves the robot
evenly from its departure to the moment it reached the end, then holds it there until it enters the
node. Without a recorded wait it equals `viz.motion.position_at`, which is unchanged.

**Events must agree with the path.** A reader takes positions from the visits and statuses from the
events. If a trace's events contradicted its visits, a viewer would show a robot charging in an
aisle or delayed while it stands. Both parsers (Python and the viewer) therefore refuse a trace
unless, for every event:
- the robot is at the event's node at that tick: in that visit, or, at the tick of an arrival, at
  the node it came from (an event can be recorded just before the robot enters the node);
- a `delay` or a `breakdown` is at the tick the robot leaves the node, and the next visit is the
  event's target;
- a corridor wait starts after that departure and before the next arrival, and ends exactly at the
  arrival at the target;
- charging starts and stops on a charger, and stops at the tick the robot leaves it; a charger trip
  aims at a charger.

The check is one binary search per event, O(E log V). It was added in the review of phase 2a and
first run over 220 real runs (all charging and recovery policies, three fleet sizes, runs cut
short, naive execution; 46,000 events of all 13 kinds): none was refused.

**What a breakdown is not.** It is not a place. The model adds time to a traversal and does not say
where on the corridor the robot loses it. The status `breakdown` (and `delay`) covers the whole
traversal, and the viewer says that the place on the edge is not modelled.

**Complexity.** Recording is O(1) per event. The reference functions scan a robot's events and are
meant to be obviously right. The viewer precomputes, per robot, the ticks at which its statuses
change, and finds the status set at a tick by binary search.

**Measured cost** (greedy dispatch, seed 0, 30 minutes of orders, on the development laptop):

| Run | Robot events | Simulation time before / now | Trace, compressed, v1 / v2 |
|---|---:|---:|---:|
| small, 3 robots, plain traffic | 0 | 39 / 41 ms | 14 / 14 kB |
| small, 3 robots, disturbed | 212 | 58 / 59 ms | 14 / 20 kB |
| medium, 8 robots, plain traffic | 0 | 171 / 173 ms | 44 / 44 kB |
| medium, 8 robots, disturbed | 852 | 428 / 440 ms | 46 / 65 kB |
| large, 25 robots, plain traffic | 0 | 1,006 / 1,025 ms | 163 / 163 kB |
| large, 25 robots, disturbed | 4,605 | 7,763 / 7,780 ms | 176 / 269 kB |

"Disturbed" means batteries, 10% of entries delayed, 1% broken down, with replanning. Times are the
fastest of 15 runs per version, in alternating processes; differences of 0.2% to 3.2% are within
what that method resolves. The largest run the service accepts (50 robots, 17,087 robot events) has
a trace of 4.59 MB, 0.77 MB compressed, against 2.06 and 0.48 MB without the additions.

**Limitations.**
- **No place on the edge** during a delay or a breakdown, and **no battery curve** while driving.
- **Free flow has none of this:** no batteries, no breakdowns, no waiting.
- **The model's simplifications remain:** only driving costs energy, charging is linear, breakdowns
  happen only at corridor entries (sections 13.6 and 13.7).
- **Reasons are not recorded.** An event says that a robot was replanned or moved, not which other
  robot caused it.
- **Trace growth is real** for disturbed runs: about half as much again when compressed.

**Interview questions.**
- *How do you know logging did not change the simulation?* Three independent checks: the old tests,
  byte-identical old fixtures after removing the additions, and published benchmark values that
  reproduce exactly.
- *Why not interpolate the battery along an edge?* Because the model charges the edge on arrival. An
  interpolated curve would be a claim the simulator never made.
- *How can events be trusted?* They add up to counters that existed before them and are computed
  separately, and every traversal's duration is fully explained by them.
- *Why is a breakdown not shown as a stopped robot?* The model has no stopping place. Showing one
  would mean inventing it.
