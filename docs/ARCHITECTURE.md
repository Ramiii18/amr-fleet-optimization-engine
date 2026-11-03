# Architecture

This is a living document. It describes what exists **now** and marks everything else as planned.

## Current state (M1, week 1)

Only the package skeleton exists so far: `src/amr_optimizer/` and a smoke test in `tests/unit/`.

## Guiding structure

- **Planners are pure functions.** A planner receives a snapshot of warehouse and fleet state and returns a
  decision, such as assignments or routes. It does not own time and does not mutate shared state.
- **The simulator owns time and state.** It executes decisions, advances time in integer ticks, and checks
  invariants after every event.
- This split keeps both sides testable on their own. It is also the boundary a future API will wrap.

## Planned packages

Each package is created only when it has real code.

| Package | Responsibility | Milestone |
|---|---|---|
| `warehouse` | Graph model, validation, map files, generator | M1 |
| `routing` | Dijkstra and A*; later space-time A* | M1, M4 |
| `domain` | Robots, orders, state machines, time units | M2 |
| `sim` | Discrete-event kernel, plan executor, invariant checks, event log | M2 |
| `dispatch` | Greedy, Hungarian, CP-SAT and local-search assignment | M2, M3 |
| `metrics` | KPI computation | M2 |
| `traffic` | Reservations, mutex zones, conflict and deadlock detection | M4 |
| `energy`, `api`, `viz` | Battery model, FastAPI service, visualization | Phase 2 / M5 |

## Modelling assumptions decided so far

These are expanded in S3, when the warehouse graph is implemented.

- The warehouse is a **directed graph**. A two-way corridor is two directed edges (D8).
- Edges have positive length, at least the straight-line distance between their endpoints (D5).
- The model has no robot physics: no acceleration, kinematics or footprint geometry. A robot is either at a
  node or traversing an edge.
- MVP robots are identical. Collisions between robots are not modelled until M4.
- Time is measured in integer ticks (D6).
