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

## 5. Where single-robot shortest paths stop being enough

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

## 6. Interview notes

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
