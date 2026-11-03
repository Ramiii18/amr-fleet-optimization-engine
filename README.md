# AMR Fleet Optimization Engine

A Python engine for dispatching transport orders to a fleet of autonomous mobile robots (AMRs),
planning routes on a warehouse graph, and managing traffic between robots, evaluated in a
reproducible simulation.

> **Status:** milestone M1 (warehouse graph and routing library) is complete. Next is M2: the
> discrete-event simulator and dispatch policies.
> Everything runs on synthetic maps and synthetic orders. This is a portfolio and learning
> project. It does not control physical robots and has not been deployed in any real fleet.

## What exists today

- **Warehouse model:** a validated, read-only directed graph of locations and connections.
- **Map generator:** synthetic grid warehouses with one-way aisles, two-way cross aisles, and dead-end
  spurs for storage, stations, chargers and parking. Three presets, up to 461 nodes, also committed as
  JSON maps in [scenarios/maps/](scenarios/maps/).
- **Routing:** Dijkstra (point-to-point and one-to-all) and A* (Euclidean or Manhattan heuristic), both
  able to avoid blocked edges.
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
git clone <repository-url>
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

## Scripts

```powershell
.venv\Scripts\python.exe scripts\generate_maps.py     # regenerate scenarios/maps from the presets
.venv\Scripts\python.exe scripts\compare_routing.py   # Dijkstra vs A* comparison (docs/benchmarks.md)
```

## Documentation

- [Project brief](docs/PROJECT_BRIEF.md): goals, scope and constraints
- [Roadmap](docs/ROADMAP.md): milestones and current status
- [Architecture](docs/ARCHITECTURE.md): modules and modelling assumptions
- [Algorithms](docs/algorithms.md): how each algorithm works, why it is correct, its limits
- [Benchmarks](docs/benchmarks.md): measured results, reported as run
- [Decisions](docs/DECISIONS.md): design decisions and their reasons
- [Progress log](docs/LEARNING_PROGRESS.md): what was built and verified, by session
