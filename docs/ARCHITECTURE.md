# Architecture

This is a living document. It describes what exists **now** and marks everything else as planned.

## Current state (M1 complete; M2 in progress, 2026-11-04)

```text
src/amr_optimizer/
  warehouse/   model.py      Node, Edge, WarehouseGraph and validation
               generator.py  synthetic grid-warehouse generator and presets
               io.py         JSON map format (strict parser, one item per line)
  routing/     route.py      Route, SearchResult, validate_route
               dijkstra.py   shortest_path, shortest_path_lengths (one-to-all)
               astar.py      A* (requires a consistent heuristic)
               heuristics.py zero, euclidean, manhattan
  domain/      timing.py     TimeModel (integer ticks), TravelTimes (per-edge travel ticks)
               orders.py     TransportOrder
               robots.py     RobotState and the robot state machine (ALLOWED_TRANSITIONS)
scripts/       generate_maps.py, compare_routing.py
scenarios/maps small.json (45 nodes), medium.json (179), large.json (461)
tests/         unit/, oracle/ (NetworkX comparisons), conftest.py (hand-checkable graphs)
```

**Dependency direction:** `domain` → `routing` → `warehouse`. Packages added later depend inwards, on
these three, never the other way round.

## Guiding structure

- **Planners are pure functions.** A planner receives a snapshot of warehouse and fleet state and returns a
  decision, such as assignments or routes. It does not own time and does not mutate shared state. Routing
  already works this way: blocked edges are passed per query, and the graph is never changed.
- **The simulator owns time and state.** It executes decisions, advances time in integer ticks, and checks
  invariants after every event (M2).
- This split keeps both sides testable on their own. It is also the boundary a future API will wrap.

## Packages

| Package | Responsibility | Status |
|---|---|---|
| `warehouse` | Graph model, validation, map files, generator | **Exists** (M1) |
| `routing` | Dijkstra and A*; later space-time A* | **Exists** (M1); space-time in M4 |
| `domain` | Robots, orders, state machines, time units | **Exists** (M2) |
| `sim` | Discrete-event kernel, plan executor, invariant checks, event log | Planned (M2) |
| `dispatch` | Greedy, Hungarian, CP-SAT and local-search assignment | Planned (M2, M3) |
| `metrics` | KPI computation | Planned (M2) |
| `traffic` | Reservations, mutex zones, conflict and deadlock detection | Planned (M4) |
| `energy`, `api`, `viz` | Battery model, FastAPI service, visualization | Phase 2 / M5 |

Each package is created only when it has real code.

## What the graph abstraction models, and what it does not

**Modelled:**
- Navigable locations, and which connections can be driven in which direction. One-way aisles are single
  directed edges (D8).
- The driven length of each connection. It is at least the straight-line distance (D5) and may be longer
  for curved paths.
- The purpose of each location: intersection, storage, station, charger or parking.
- Dead-end endpoints in generated maps, so parked robots don't block aisles.

**Not modelled (yet):**
- **Speed profiles.** Time is integer ticks (D6). A robot drives every edge at one constant speed, and
  each edge's time is rounded up to whole ticks (D12).
- **Robot physics:** acceleration, braking, turning radius, rotate-in-place time at intersections. Turn
  penalties are a known future extension.
- **Robot footprint and safety separation.** In M4, one robot per node and per corridor stands in for
  them.
- **Other robots, occupancy and capacity.** These arrive with traffic management in M4.
- **Per-edge speed limits and floor conditions.**
- **Certified safety functions.** Safety scanners, protective fields and emergency stops are out of
  scope. This is a planning and simulation abstraction, not a safety system.
