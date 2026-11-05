# AMR Fleet Optimization Engine

[![CI](https://github.com/Ramiii18/amr-fleet-optimization-engine/actions/workflows/ci.yml/badge.svg)](https://github.com/Ramiii18/amr-fleet-optimization-engine/actions/workflows/ci.yml)

A Python engine for dispatching transport orders to a fleet of autonomous mobile robots (AMRs),
planning routes on a warehouse graph, and managing traffic between robots, evaluated in a
reproducible simulation.

> **Status:** milestones M1 (routing library) and M2 (simulator, dispatch policies, KPIs) are complete.
> M3 (look-ahead and multi-order planning with CP-SAT) is in progress.
> Everything runs on synthetic maps and synthetic orders. This is a portfolio and learning
> project. It does not control physical robots and has not been deployed in any real fleet.

## What exists today

- **Warehouse model:** a validated, read-only directed graph of locations and connections.
- **Map generator:** synthetic grid warehouses with one-way aisles, two-way cross aisles, and dead-end
  spurs for storage, stations, chargers and parking. Three presets, up to 461 nodes, also committed as
  JSON maps in [scenarios/maps/](scenarios/maps/).
- **Routing:** Dijkstra (point-to-point and one-to-all) and A* (Euclidean or Manhattan heuristic), both
  able to avoid blocked edges.
- **Domain model (M2):** integer simulation ticks with per-edge travel times, transport
  orders, and robots whose phases follow an explicit, fully tested state machine.
- **Scenarios (M2):** seeded, reproducible order streams and fleet start positions; the same seed gives
  the same scenario on Windows and Linux.
- **Simulator (M2):** a deterministic discrete-event simulator with a pure-function dispatch interface,
  invariant checks after every tick, and a per-order event log. Robots don't interact yet (no
  collisions or congestion); that comes with traffic management in M4.
- **Dispatch (M2):** greedy (oldest order, nearest robot) and optimal assignment (Hungarian problem,
  solved with scipy), with optional decision rounds.
- **KPIs and comparison (M2):** waiting, completion (mean, P95 and maximum), utilization and empty
  travel, from the event log. Policies are compared on paired seeds, with confidence intervals. In M2,
  optimal matching gave no measurable gain over greedy; [benchmarks.md](docs/benchmarks.md) explains
  why.
- **Planning dispatch (M3):** at each decision, plan every robot (busy ones from where and when they
  will finish) and the oldest 10 orders, then start only the idle robots' first jobs (a receding
  horizon). Plans come from cheapest insertion or from an exact CP-SAT model with a deterministic time
  limit, checked against brute force on 300 small problems. At high load, CP-SAT planning cut mean
  completion time by about a quarter against greedy, but made the worst case worse;
  [benchmarks.md](docs/benchmarks.md) has the numbers.
- **Verification:** hand-computed cases, oracle tests against NetworkX on 60 random graphs, and
  route validation.

```python
from amr_optimizer.routing import astar, manhattan
from amr_optimizer.warehouse import NodeId, load_graph

graph = load_graph("scenarios/maps/large.json")
result = astar(graph, NodeId("ST00"), NodeId("S05-07"), manhattan)
print(result.route.nodes, result.route.length_m)  # 48.0 m: aisle 5 runs downward,
# so the route goes up aisle 4, crosses at row 10, and comes back down aisle 5.
```

## Setup (Windows, PowerShell)

Requires Python 3.13 and Git.

```powershell
git clone https://github.com/Ramiii18/amr-fleet-optimization-engine.git
cd amr-fleet-optimization-engine
py -3.13 -m venv .venv
.venv\Scripts\python.exe -m pip install --upgrade pip
.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

On Linux or macOS, use `python3.13 -m venv .venv` and `.venv/bin/python` instead.

## Checks

Run all four before every commit:

```powershell
.venv\Scripts\python.exe -m pytest
.venv\Scripts\python.exe -m ruff check .
.venv\Scripts\python.exe -m ruff format --check .
.venv\Scripts\python.exe -m mypy
```

## Run a simulation

```powershell
.venv\Scripts\amr-sim.exe --map large --robots 10 --orders-per-hour 300 --policy cpsat
.venv\Scripts\amr-sim.exe --help   # policies, --json for JSON, and the other options
```

## Scripts

```powershell
.venv\Scripts\python.exe scripts\generate_maps.py     # regenerate scenarios/maps from the presets
.venv\Scripts\python.exe scripts\compare_routing.py   # Dijkstra vs A* comparison (docs/benchmarks.md)
.venv\Scripts\python.exe scripts\compare_dispatch.py  # dispatch policies in simulation (docs/benchmarks.md)
.venv\Scripts\python.exe scripts\compare_planning.py  # greedy vs insertion vs CP-SAT planning (docs/benchmarks.md)
```

## Documentation

- [Project overview](docs/PROJECT_OVERVIEW.md): goals, scope and constraints
- [Roadmap](docs/ROADMAP.md): milestones and current status
- [Architecture](docs/ARCHITECTURE.md): modules and modelling assumptions
- [Algorithms](docs/algorithms.md): how each algorithm works, why it is correct, its limits
- [Benchmarks](docs/benchmarks.md): measured results, reported as run
- [Decisions](docs/DECISIONS.md): design decisions and their reasons
- [Progress log](docs/LEARNING_PROGRESS.md): what was built and verified, by session

## License

[MIT](LICENSE)
