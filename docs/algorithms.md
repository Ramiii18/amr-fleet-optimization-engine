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
- Robots don't interact: there are no collisions, no blocking and no waiting for each other. This is
  the documented MVP simplification; traffic arrives in M4, and the difference in KPIs will measure the
  cost of congestion.
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
- **completion time** (lead time): from release until delivery;
- **throughput**: delivered orders per hour, from tick 0 to the last event;
- **utilization**: the share of robot time between assignment and delivery;
- **empty and loaded distance**: metres driven to pickups, and with a load. The empty share is the part
  dispatch can influence.

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

---

## 11. Where single-robot shortest paths stop being enough

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

## 12. Interview notes

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

**Why paired seeds and confidence intervals?**
Different order streams vary far more than policies do. Running every policy on the same streams and
comparing per-seed differences removes that variation. The interval shows whether the remaining
difference is distinguishable from zero at the sample size used.
