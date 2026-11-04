# Design decisions

A lightweight decision log (ADR-style). Each entry records what was decided, why, and what it costs.
Possible statuses: **Accepted**, **Proposed**, **Superseded by Dn**.

---

## D1: Python 3.13, venv + pip, one `pyproject.toml`
**Status:** Accepted (2026-11-03)
**Why:** Python 3.13 is installed and current, and OR-Tools publishes Windows wheels for it: versions 9.12–9.15
were checked with `pip index versions ortools`. Plain venv and pip teach the mechanics that tools like uv
or Poetry automate.
**Consequences:** There is no lock file yet, so benchmark runs must record the exact package versions they
used. Switching to uv later is a small change.

## D2: Our own graph implementation; NetworkX only as a test oracle
**Status:** Accepted (2026-11-03)
**Why:** Implementing the graph and the search algorithms is the skill this project exists to demonstrate.
Space-time search later also needs tight control over the inner loop. NetworkX is mature and independent
of our code, which makes it a good reference to test against.
**Consequences:** Our implementation needs thorough tests. `src/` never imports networkx.

## D3: Frozen dataclasses in the core; Pydantic only at boundaries
**Status:** Accepted (2026-11-03)
**Why:** Immutable objects stop algorithms from accidentally mutating shared state. Dataclasses are
standard-library and fast. Validating external input (files, API requests) is a boundary concern.
**Consequences:** Domain invariants are enforced in constructors (for example, `WarehouseGraph`
validation), not by a serialization library.

## D4: Expected outcomes are return values; invalid input raises an exception
**Status:** Accepted (2026-11-03)
**Why:** An unreachable target, such as one behind a blocked aisle, is a normal operational situation that
the caller must handle, so it returns `route=None`. An unknown node id is an input or programming error,
so it raises an exception.
**Consequences:** Callers must check `result.route is None` explicitly, and mypy forces them to.

## D5: Edge length must be > 0 and ≥ the straight-line distance between its endpoints
**Status:** Accepted (2026-11-03)
**Why:** A positive length rules out zero-duration moves, where a robot would "teleport". A length at least
equal to the straight-line distance guarantees that the Euclidean A* heuristic never overestimates. That
property is called admissibility, and it is what makes A* return optimal routes.
**Consequences:** Map validation rejects edges that break either rule, so the generator must produce
coordinates consistent with edge lengths.

## D6: Simulation and reservation time are integer ticks
**Status:** Accepted (2026-11-03), to be implemented from week 3
**Why:** Reservation tables and conflict checks compare times for equality and overlap, and floats make
that fragile (`0.1 + 0.2 != 0.3`). Space-time search needs discrete time anyway.
**Consequences:** Edge durations are rounded up with `ceil(length / speed / tick)`, which errs on the
safe side. The tick length is a documented configuration value.

## D7: Determinism through injected random generators and deterministic tie-breaking
**Status:** Accepted (2026-11-03)
**Why:** Benchmarks and bug reports must be reproducible. Global random state is hidden shared state.
**Consequences:** Every function that needs randomness takes a `random.Random`. Priority queues break ties
with an insertion counter.

## D8: Directed edges only
**Status:** Accepted (2026-11-03)
**Why:** One-way aisles are common in warehouses, and a two-way corridor is simply two directed edges.
Algorithms stay simple and uniform.
**Consequences:** Detecting head-on conflicts on a single-lane two-way corridor requires knowing that two
directed edges share one physical corridor. That shared resource is added in the traffic phase (M4).

## D9: The warehouse generator is deterministic, with no random seed
**Status:** Accepted (2026-11-03). The original plan proposed a seeded generator.
**Why:** Nothing in the layout needs randomness. A map that is a pure function of its spec is easier to
reason about, diff and reproduce. Experiment variability comes from seeded order and event streams (M2+),
not from the map.
**Consequences:** The example maps in `scenarios/maps/` are regenerated with `scripts/generate_maps.py`,
and a test fails if a committed map no longer matches the generator. If randomized layouts are needed
later, they will take an injected `random.Random` (D7).

## D10: A* uses a closed set and never reopens nodes, so it requires a consistent heuristic
**Status:** Accepted (2026-11-03)
**Why:** With a consistent heuristic, the first expansion of a node is final. That gives a simpler and
faster search with the same optimality guarantee. Euclidean distance is consistent on every valid map
(D5 plus the triangle inequality). Manhattan distance is consistent on the axis-aligned generated maps.
**Consequences:** A heuristic that is admissible but not consistent can produce suboptimal routes. A test
documents this with an inflated heuristic. If such heuristics are ever needed, add node reopening and
benchmark it.

## D11: CI runs the four local checks on both Windows and Linux
**Status:** Accepted (2026-11-04)
**Why:** Development happens on Windows, while Docker and any deployment will run on Linux. Running both
catches platform differences early. M1 already hit one: map files were written with CRLF line endings on
Windows.
**Consequences:** Every run starts two jobs. They are free on public repositories; on private ones,
Windows jobs consume the monthly minutes faster. CI uses exactly the same commands as the README, so a
local pass predicts a CI pass.
