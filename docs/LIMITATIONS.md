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
  - robot faults, blocked aisles and replanning during execution (P1 added random delays and robust
    execution);
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
- **Delays: robust execution, but no replanning (Phase 2, P1).** With delays, robots keep the planned
  order at every node and corridor (D25), which is safe and cannot deadlock.
  - A late robot passes its delay on to the robots planned behind it. Nobody replans or reorders.
  - New plans assume that robots already moving are on time.
  - The delay model is independent per edge, with no correlated slow-downs and no breakdowns.
  - The M4 and M5 benchmarks ran without delays.
- **Well-formed layouts only.** Planning is guaranteed to succeed because every stopping place is a
  dead-end spur. On maps without that property, prioritized planning can fail, and the simulator stops
  with an error rather than hiding it.
- **The measured cost of traffic mixes two effects.** It includes waiting for other robots and the short
  drive to a holding spot after each delivery, which free flow does not make. They are not separated.

## Batteries and charging (Phase 2, P2)

- **Synthetic battery parameters.** A full battery lasts one hour of driving and charges in 20 minutes,
  chosen so that charging matters within a one-hour run (D26). The measured costs of charging scale
  with these choices, and are not predictions for any real robot.
- **Start-of-shift runs.** Runs last one hour from random start levels between 30% and 100%. A full
  shift in steady state was not simulated.
- **Simple energy model.** Only driving uses energy, in proportion to distance; loading, waiting and
  charging losses are free. Charging is linear, with no slowdown near full.
- **Threshold rules, not optimization.**
  - Robots go to the nearest free charger by travel time, and there is no queue at chargers.
  - The dispatch policies see when robots out of service will be back, but not battery levels.
  - Energy-aware assignment and a CP-SAT charging schedule (backlog) were not tried.
  - Only three charging rules were compared, with fixed thresholds.
- **Battery safety is checked, not proved.**
  - Only robots above a threshold derived from the map receive jobs (D26). The threshold covers four
    shortest drives between endpoints.
  - With traffic, an itinerary may detour, and a low robot waiting for a charger may be moved out of
    the way. Neither is bounded.
  - An invariant stops the run if a battery would go below zero. It never fired; the lowest level in
    the benchmark was 18.2%.

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
