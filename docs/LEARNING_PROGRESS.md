# Progress log

## Session log

Test results are recorded exactly as they appeared when the tests ran.

| Date | Session | Built | Tests (as run) | Concepts | Open items |
|---|---|---|---|---|---|
| 2026-11-03 | S0 Planning | Assessment, MVP definition, roadmap, decisions D1–D8 | — | — | GitHub account and repo visibility; find Project A source before week 6 |
| 2026-11-03 | S1 Environment | Scaffold by Claude; owner created `.venv`, did the editable install, set repo-local git identity, made the first commit (`421b6dd`), renamed the branch to `main`; `.gitattributes` added (LF everywhere) | Owner's `.venv`: pytest 1 passed; ruff check clean; ruff format clean; mypy clean | venv vs global env, `sys.path`, `src/` layout, editable install, git config levels, line endings | Written answers deferred by owner (revisit at M1 review); rebuild-from-README timing not yet done |
| 2026-11-03 | Method change | The owner switched to build-first: Claude implements; explanations go in `docs/algorithms.md` and `docs/DECISIONS.md` instead of exercises | — | — | — |
| 2026-11-03 | M1 build | Warehouse model and validation; Dijkstra (point-to-point, one-to-all); A* with three heuristics; `validate_route`; generator with 3 presets; JSON map format; example maps; comparison script; docs (algorithms, benchmarks, architecture, decisions D9–D10) | 446 passed; ruff check clean; ruff format clean; mypy strict clean (21 files) | Adjacency lists, graph invariants, Dijkstra invariant and lazy deletion, A* admissibility vs consistency, oracle testing, deterministic generation, line endings in file output | GitHub account and repo visibility, needed for CI in M2 |
| 2026-11-04 | Publishing | Git history prepared for a public repository; MIT license; the owner pushed `main`; CI on Windows and Linux (D11) | First CI run, on `1b465b3`: passed on `windows-latest` and `ubuntu-latest` | Rewriting history before a first push, CI matrices, GitHub noreply addresses | — |
| 2026-11-04 | M2.1 Time model | `domain/timing.py`: `TimeModel` (integer ticks, rounding up, float-noise snap) and `TravelTimes` (per-edge ticks, arrival offsets, exactness flag); decision D12; algorithms section 5 | 509 passed; ruff check clean; ruff format clean; mypy strict clean (24 files) | Why integer time, conservative rounding, floating-point noise, per-edge vs whole-route rounding, why shortest in metres is not always fastest in ticks | Next: robot and order model with an explicit state machine |

## Understanding checks

Written answers to interview questions, timed re-implementations and similar evidence.

| Date | Topic | Evidence |
|---|---|---|
