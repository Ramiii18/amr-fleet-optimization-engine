# AMR Fleet Optimization Engine

A Python engine for dispatching transport orders to a fleet of autonomous mobile robots (AMRs),
planning routes on a warehouse graph, and managing traffic between robots, evaluated in a
reproducible simulation.

> **Status:** early development, milestone M1 (warehouse graph and routing library).
> Everything runs on synthetic maps and synthetic orders. This is a portfolio and learning
> project. It does not control physical robots and has not been deployed in any real fleet.

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

## Documentation

- [Project brief](docs/PROJECT_BRIEF.md): goals, scope and constraints
- [Roadmap](docs/ROADMAP.md): milestones and current status
- [Architecture](docs/ARCHITECTURE.md): modules and modelling assumptions
- [Decisions](docs/DECISIONS.md): design decisions and their reasons
- [Learning progress](docs/LEARNING_PROGRESS.md): session log
