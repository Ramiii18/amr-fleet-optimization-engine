# Architecture

This is a living document. It describes what exists **now** and marks everything else as planned.

## Current state (M1 to M5, Phase 2 and replay traces; 2026-11-11)

```text
src/amr_optimizer/
  warehouse/   model.py      Node, Edge, WarehouseGraph and validation
               generator.py  synthetic grid-warehouse generator and presets
               io.py         JSON map format (strict parser, one item per line)
  routing/     route.py      Route, SearchResult, validate_route
               dijkstra.py   shortest_path, shortest_path_lengths (one-to-all)
               connectivity.py  strongly connected components (iterative Tarjan, D39)
               astar.py      A* (requires a consistent heuristic)
               heuristics.py zero, euclidean, manhattan
  domain/      timing.py     TimeModel (integer ticks), TravelTimes (per-edge travel ticks)
               orders.py     TransportOrder
               robots.py     RobotState and the robot state machine (ALLOWED_TRANSITIONS)
               energy.py     BatterySpec, EnergyModel: integer energy units, safety bound;
                             ChargingPolicy (Phase 2)
  scenario/    model.py      Scenario: validated input of one simulation run
               generator.py  seeded order stream (Bernoulli arrivals), fleet placement
               io.py         scenario files: your own map, robots and orders (D32)
  sim/         simulator.py  discrete-event loop, executor, invariant checks; free flow or traffic;
                             batteries and charging; breakdowns, re-timing and recovery
                             (Phase 2)
               traffic_control.py  plans jobs and moves against the reservation table (M4)
               policy.py     dispatch interface: DispatchSnapshot, Assignment, DispatchPolicy
               planner.py    TravelPlanner: cached shortest routes and travel ticks
               log.py        the order log (six steps per order), and robot events: charging,
                             service state, delays, breakdowns, waits (observations only, D35)
  dispatch/    greedy.py     GreedyDispatch: oldest order first, nearest idle robot
               hungarian.py  HungarianDispatch: optimal matching to the oldest orders
               matching.py   min_cost_matching (scipy linear_sum_assignment)
               horizon.py    bounded-horizon planning model: robot availability, HorizonProblem,
                             plan evaluation and cost (total or worst first), first jobs (M3)
               insertion.py  cheapest-insertion plan; InsertionDispatch (receding horizon)
               cpsat.py      CP-SAT giant-tour model, solve_plan, CpSatDispatch (receding horizon)
  traffic/     paths.py      TimedPath, Visit, Corridor, Occupancy (closed at nodes, open on corridors)
               reservations.py  ReservationTable: reservations, holds, safe intervals (M4)
               conflicts.py  independent conflict checker (vertex, head-on, following)
               sipp.py       ItineraryPlanner: safe-interval path planning through stops to a hold
               execution.py  executing plans under delays: naive or in planned order (Phase 2)
  viz/         motion.py     robot position and activity at any tick (pure functions)
               plots.py      map, robots, scaling chart, animation (matplotlib; `viz` extra)
  metrics/     kpis.py       compute_kpis from the event log; nearest-rank percentile;
                             compute_charging_kpis for runs with batteries (Phase 2)
               compare.py    paired_difference: per-seed differences with a t confidence interval
               timeline.py   order_timelines: each order's steps, times and distances (CSV in the CLI)
  layout/      model.py      Layout: a floor plan as a text grid, one character per cell (D38)
               compiler.py   compile_layout: one node per cell, one-way rule, ids from the cell
               io.py         layout files (strict parser, one row per line); one loader for map
                             and layout files
               wellformed.py check_well_formed: is a map fit for the traffic planner (D39)
               __main__.py   `python -m amr_optimizer.layout compile|check` (D40)
  replay/      trace.py      ReplayTrace: a finished run as a versioned record for viewers (map,
                             visits, orders, event log, KPIs, conflicts); strict parser (D33)
               schema.py     the same format as a JSON Schema, for other languages and the API docs
               state.py      the reference for a robot's position (with waits at the end of
                             corridors), statuses and battery level at any tick (D35)
  api/         models.py     Pydantic request and response models: the only place Pydantic is used
               work.py       CPU-bound work for worker processes: requests -> engine types -> results
               app.py        FastAPI app: health, maps, policies, /plan, simulation jobs and their
                             replay traces; a process pool per app
               traces.py     stored traces, bounded by count and size; Accept-Encoding (D33)
               serve.py      `amr-api`: runs the service with Uvicorn (`api` extra; Phase 2)
  vda5050/     messages.py   VDA 5050 3.0.0 messages (order, state, instant actions, connection),
                             topics, headers; plain dataclasses with to_json/from_json (Phase 2)
               orders.py     itineraries as orders, base and horizon, order updates, the rules
               mock.py       message bus (topics, log) and mock robots that speak the interface
               fleet.py      fleet control: releases bases under the planned order; runs of jobs
  cli.py, __main__.py        `amr-sim` / `python -m amr_optimizer`: one scenario, one policy, KPIs
web/           the browser viewer (TypeScript, React, Vite; D34). It replays traces and never simulates
  src/lib/     trace.ts (the format and its check), motion.ts (position and activity: the port of
               viz/motion.py), state.ts (statuses and battery level: the port of replay/state.py),
               replay.ts, playback.ts (the clock), api.ts, runForm.ts, view.ts
  src/render/  the map and the fleet on a 2D canvas
  src/components/  map, playback bar, run panel, details
scripts/       dev.ps1 (starts the service and the viewer), check_published_results.py (re-runs
               published benchmarks and compares), measure_observability.py, generate_maps.py, compare_routing.py, compare_dispatch.py, compare_planning.py,
               compare_traffic.py, benchmark_scaling.py, make_figures.py, compare_delays.py,
               compare_charging.py, compare_breakdowns.py, vda5050_demo.py, make_replay_fixtures.py,
               measure_cell_size.py
docs/results   scaling.csv, breakdowns.csv (per-run benchmark results, the source of the figures)
docs/figures   figures and animation, regenerated by scripts/make_figures.py
layouts/examples  synthetic layout files (D38); layouts/private/ is git-ignored for real plans
scenarios/maps small.json (45 nodes), medium.json (179), large.json (461)
scenarios/examples  scenario files (D32), each run by the tests
tests/         unit/, oracle/ (NetworkX comparisons), conftest.py (hand-checkable graphs)
tests/data     vda5050/ (the official schemas), replay/ (golden fixtures for the viewer, D34)
Dockerfile     the service as a container image (Python 3.13 slim, unprivileged user, D29)
```

**Dependency direction:** `dispatch` → `sim` → `traffic`, `scenario` → `domain` → `routing` →
`warehouse`. Dispatch policies implement the interface in `sim/policy.py`; the simulator never imports a
policy. `traffic` knows nothing about the simulator. `replay` reads a finished run (`sim`, `metrics`,
`traffic`) and the simulator never imports it, so recording a trace cannot change a run (D33). `layout`
sits on `domain` and `routing`: it compiles layout files into graphs and checks any map before a run
with traffic. `api`
sits on top of everything and nothing imports it (D29). The command line uses `replay` too.

**The viewer is outside this graph.** `web/` shares no code with the Python package. It depends on
two contracts: the HTTP API, and the trace format. Both are checked from both sides: Python writes
golden fixtures that the viewer's tests must reproduce, and a Python test pins the part of the
OpenAPI document that the viewer's form reads.

**Runtime dependencies:** numpy and scipy, for optimal assignment (D16), and OR-Tools, for the CP-SAT
planner (D20). Everything else is the standard library.

## Guiding structure

- **Planners are pure functions.** A planner receives a snapshot of warehouse and fleet state and returns a
  decision, such as assignments or routes. It does not own time and does not mutate shared state. Routing
  already works this way: blocked edges are passed per query, and the graph is never changed.
- **The simulator owns time and state.** It executes decisions, advances time in integer ticks, and checks
  invariants after every tick that has events (D15).
- This split keeps both sides testable on their own. It is also the boundary a future API will wrap.

## Packages

| Package | Responsibility | Status |
|---|---|---|
| `warehouse` | Graph model, validation, map files, generator | **Exists** (M1) |
| `routing` | Dijkstra and A*; later space-time A* | **Exists** (M1); space-time in M4 |
| `domain` | Robots, orders, state machines, time units | **Exists** (M2) |
| `scenario` | Validated simulation input; seeded order streams and fleet placement | **Exists** (M2) |
| `sim` | Discrete-event kernel, plan executor, invariant checks, event log | **Exists** (M2) |
| `dispatch` | Greedy, Hungarian, bounded-horizon planning (insertion, CP-SAT); later local search | **Exists** (M2: greedy, Hungarian; M3: planning model, insertion, CP-SAT) |
| `metrics` | KPI computation | **Exists** (M2) |
| `traffic` | Timed paths, reservations, conflict checking, SIPP itinerary planning, ordered execution, re-timing | **Exists** (M4; Phase 2: delays, re-timing); mutex zones in the backlog |
| `viz` | Figures and animations of maps, runs and benchmark results (optional extra, matplotlib) | **Exists** (M5) |
| `replay` | Replay traces: a finished run as a versioned, validated record for viewers | **Exists** (digital twin, phase 1a) |
| `api` | HTTP service: planning, simulation jobs and their replay traces (optional extra, FastAPI and Uvicorn) | **Exists** (Phase 2, P4; traces in phase 1a of the digital twin) |
| `web/` | Browser viewer that replays traces (React, TypeScript, Vite, a 2D canvas; D34). Not a Python package: it talks to `api` over HTTP only | **Exists** (digital twin, phase 1b) |

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
- **Other robots, occupancy and capacity**, in the default free-flow mode. With traffic (M4), each node
  and each corridor holds one robot at a time (D23); finer capacity, such as two robots following each
  other closely along one corridor, is not modelled.
- **Per-edge speed limits and floor conditions.**
- **Certified safety functions.** Safety scanners, protective fields and emergency stops are out of
  scope. This is a planning and simulation abstraction, not a safety system.
