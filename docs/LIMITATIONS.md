# Limitations

What the engine does not model, does not prove, or has not been tested on. Each item says why it
matters and where it is discussed. Results elsewhere in this repository should be read with these
limits in mind.

## Scope

- **Not a real system.** Everything runs on synthetic maps and synthetic orders. The engine does not
  control physical robots, has not been deployed in any fleet, and reproduces no vendor or customer
  implementation.
- **Not a safety system.** Safety scanners, protective fields and emergency stops are out of scope. The
  traffic model prevents conflicts at the level of the graph and the schedule, not physical
  collisions.
- **Not built yet (Phase 2):**
  - battery and charging;
  - robot faults, blocked aisles and replanning during execution;
  - order priorities and deadlines;
  - mutex zones;
  - an API service;
  - containerized deployment.

  See the [roadmap](ROADMAP.md).

## Model of the warehouse and the robots

- **Robots are points on a graph.** They occupy nodes and corridors, with no footprint, acceleration,
  braking or turning time. Every robot drives every edge at one constant speed, and time is rounded up
  to 0.1 s ticks per edge ([architecture](ARCHITECTURE.md), D12).
- **Identical robots.** No heterogeneous fleet, no payload limits, one order per robot at a time.
- **Fixed handling times.** Loading and unloading always take the same time.

## Traffic

- **Conservative capacity (D23).** A corridor holds one robot even when two could safely follow each
  other, and a node needs one tick of clearance. Part of the measured cost of traffic comes from this
  modelling choice.
- **Prioritized planning (D24).** Robots plan in decision order, first come, first served. A
  fleet-wide planner, such as conflict-based search, could reduce waiting; it was not tried.
- **Exact execution.** Robots are never delayed, so plans never break. Real delays would need an
  execution policy that keeps the planned order of robots at each resource; this is in the Phase 2
  backlog.
- **Well-formed layouts only.** Planning is guaranteed to succeed because every stopping place is a
  dead-end spur. On maps without that property, prioritized planning can fail, and the simulator stops
  with an error rather than hiding it.
- **The measured cost of traffic mixes two effects.** It includes waiting for other robots and the short
  drive to a holding spot after each delivery, which free flow does not make. They are not separated.

## Dispatch and optimization

- **No forecasting.** Planners know only released orders. They plan the oldest 10 pending orders over a
  receding horizon (D18).
- **Free-flow estimates inside the planners.** With traffic, the planners know exactly when busy robots
  become free, but they estimate future trips without congestion.
- **Myopic objectives.** Total completion time starves distant orders. The worst-first objective
  protects only the single longest-waiting order at each decision (D22).
- **Solver limits.** CP-SAT stops at a deterministic work limit. At high load about 15% of searches with
  the total-completion objective stopped before proving optimality, and their bounds are weak. Every
  stop is counted in the benchmarks (D20).
- **Latency is not a real-time guarantee.** Decision times were measured on one laptop, with several
  benchmark processes sharing the CPU. Deterministic limits keep plans reproducible, but they bound
  work, not wall time. A production system would need wall-clock limits, at the cost of
  reproducibility.

## Evaluation

- **One map.** All comparisons use the generated large map (461 nodes, 6 stations): uniform demand,
  retrieval orders from storage to stations. Other layouts, clustered demand and larger fleets are
  untested.
- **20 seeds per configuration.** Confidence intervals are reported; differences smaller than about a
  second, or a few seconds at high load, cannot be resolved. Worst-case values are means of per-run
  maxima.
- **Some comparisons are estimates, not tests.** Where a benchmark reports two policies only against a
  common baseline, the difference between them is not separately tested. The benchmark text says so
  where it applies.
- **Benchmarks were produced on Windows.** CI runs every test on Windows and Linux, and a pinned
  scenario guards cross-platform determinism. The benchmark tables themselves were produced on Windows
  only.
- **The palette validator was not run.** The figures use the first three colours of the reference
  palette unchanged. The palette documents them as validated for colour-vision deficiency in light and
  dark mode, but the validator script was not run here, because it needs Node.js, which is not part of
  the project's toolchain.
