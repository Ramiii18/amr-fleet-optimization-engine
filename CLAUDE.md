# CLAUDE.md: guidance for Claude Code sessions in this repository

## What this project is
This is a portfolio and learning project: a synthetic AMR fleet dispatch, routing and traffic engine with a
discrete-event simulator. The owner's full brief is `docs/PROJECT_BRIEF.md`. It is **local only and
git-ignored** because it contains personal and job-search details. The public summary is
`docs/PROJECT_OVERVIEW.md`. At the start of every session, read `docs/ROADMAP.md` (current milestone) and
`docs/LEARNING_PROGRESS.md` (last session, open items).

The repository is public. Never commit `docs/PROJECT_BRIEF.md`, and never copy personal, employer, vendor,
location or job-search details into tracked files or commit messages.

## Working agreement (build-first, since 2026-11-03)
- Claude implements the project directly: algorithms, tests, tooling and docs. Do not set exercises or quizzes.
- Explain every design and algorithm choice briefly: the problem, the approach, complexity, invariants and
  limitations. Record the explanation in `docs/algorithms.md` and record decisions in `docs/DECISIONS.md`, so
  the owner can defend the work in technical interviews.
- Work in small, reviewable increments. Each increment must pass all four checks and get its own commit.
  Push only when the owner asks.
- After each working session, add a short entry to `docs/LEARNING_PROGRESS.md` (the progress log).

## Honesty rules (non-negotiable)
- Never claim that tests or benchmarks passed, or report numbers, unless code was actually run to produce them.
- Never hide infeasibility, solver timeouts or regressions. Never hardcode outputs to make a demo look successful.
- Use synthetic data only. Do not reproduce employer, customer or vendor implementations or specifications.
- Never imply that this engine controls real robots or was deployed in a real fleet.

## Environment
- Windows 11, PowerShell. Python 3.13 lives in `.venv`. Never install into the global Python: it is heavily
  populated with unrelated packages.
- Run tools through the venv interpreter; no activation is needed:
  - `.venv\Scripts\python.exe -m pytest`
  - `.venv\Scripts\python.exe -m ruff check .`
  - `.venv\Scripts\python.exe -m ruff format --check .`
  - `.venv\Scripts\python.exe -m mypy`

## Code conventions
- `src/` layout with package `amr_optimizer`. Tests live under `tests/` (pytest, importlib import mode).
- Type hints everywhere. mypy runs in strict mode (tests may omit annotations).
- Core domain and algorithms use frozen dataclasses (`slots=True`) and the standard library first.
  Pydantic is used only at boundaries (files, API).
- NetworkX is allowed in tests only, as a reference oracle. Never import it in `src/`.
- Randomness: inject a `random.Random(seed)`. Never use the global `random` state.
- Simulation and reservation time are integer ticks, never floats (DECISIONS D6).
- Expected outcomes, such as an unreachable target, are return values. Invalid input raises an exception (D4).
- No placeholder modules or empty packages. A package exists once it has real code.
- Record every non-trivial design decision in `docs/DECISIONS.md`.
