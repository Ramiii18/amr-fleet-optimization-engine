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
  - blocked aisles: corridors closed without a robot in them (P3 added breakdowns and replanning);
  - order priorities and deadlines;
  - mutex zones.

  See the [roadmap](ROADMAP.md). The service has a container image, which is built and checked in
  CI only (see "The HTTP service" below).

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
  dead-end spur, the map is strongly connected and there are enough holding spots (3N − 1 for N
  robots). The command line and the lab check this before a run with traffic and refuse a map or a
  fleet that breaks it (D39). The guarantee is sufficient, not necessary. The service does not
  check: it accepts presets only, but allows fleets above what the small (6 robots) and medium (26)
  maps guarantee. Robot start positions in scenario files are not checked.
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

## Breakdowns and replanning (Phase 2, P3)

- **Announced breakdowns with known repair times.** A breakdown is known the moment it happens, with
  its exact repair time (D28). A real fleet manager learns late and estimates the repair, so its
  re-timed plans would be less accurate than here.
- **A simple breakdown model.** Breakdowns happen only while a robot enters a corridor: independent
  draws per entry, and uniform repair times of 30 to 180 s. None happens at a node or during loading.
- **A broken robot keeps its order.** Its job is never reassigned, even when it breaks down on the
  way to the pickup.
- **Only breakdowns trigger recovery.** Delays are not announced, so a robot that is merely late is
  not re-timed or replanned. New plans keep assuming it is on time (P1) until the next breakdown.
- **Prioritized replanning.** Delayed robots replan one after another, so the order matters.
  - Each kept plan is better for its own robot, but the fleet as a whole is not optimized.
  - A plan re-timed behind a robot that is then replanned faster stays pessimistic until the next
    breakdown.
- **Distances are as planned.** The empty and loaded distances in the KPIs come from the routes
  planned at assignment and loading, so a replanned route changes the trajectory but not those
  distances.
- **Two bugs that only a stress test found.** Both are fixed and covered by tests (D28). The design
  argument, one consistent clock and robots never early, is checked by invariants and tests, not
  proved formally.

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

## The VDA 5050 adapter (Phase 2, P5)

- **A mock, checked against the specification, not against robots.** Messages follow VDA 5050 3.0.0,
  and the tests validate them against its official schemas. No real robot, broker or vendor
  implementation was involved, and none was tested against.
- **One job per robot.** Fleet control sends each robot one planned job. Continuous dispatch through
  the adapter was not built.
- **Ideal messaging.** There is no network latency, no message loss and no reconnection: messages
  arrive in the tick they are sent.
- **Ideal mock robots.** They have constant speed and exact positions, and report no errors.
  - Instant actions, factsheets, visualization, zones and corridors are not used.
  - The mock robots report a constant charge.
- **Progress is not proved.** Node-only stopping can create cyclic waits. Fleet control resolves
  rotations, robots waiting for each other around a cycle (D31), and raises an error on any other
  stall. There is no general proof that every plan completes under the adapter's rules.
- **Stopping only at nodes costs time.** As the specification requires, robots wait at nodes, not at
  the end of corridors. Measured: 0.6 to 8.5 s later than planned on average, more with more robots
  (benchmarks).

## The HTTP service (Phase 2, P4)

- **A local demonstration, not a production service.** There is no authentication and no rate
  limiting beyond bounded request sizes. The server binds to 127.0.0.1 by default (D29).
- **Maps by preset name only.** Clients cannot send their own maps. That keeps file paths out of
  requests, but limits the service to the generated layouts.
- **One decision per `/plan` request, from a snapshot the client describes.** Busy robots are given
  only as "free at this node from this tick". The service keeps no state between decisions: no
  reservations, no traffic, no plans being executed.
- **The container is built and checked in CI only.** It was never run on the development machine:
  Docker Desktop on Windows needs WSL2. The image runs one server process, without TLS.
- **Jobs live in memory.** Simulation jobs are lost when the server stops. Only the last 100 finished
  jobs are kept, and a running job cannot be cancelled.
- **Traces live in memory too, and fewer of them.** Only the 20 most recent replay traces are
  kept, within 32 MB of compressed data. An older trace is gone (410) while its job and KPIs remain;
  the run has to be made again to get it back.

## Replay traces (digital twin, phase 1a)

- **A record of a simulated run, nothing more.** A trace replays what the simulator computed on a
  synthetic map. It is not a record of any real robot, and "no conflicts" in a trace is a statement
  about nodes and corridors of the graph model, not about physical clearance.
- **Version 1 traces record three activities only.** They have the six steps of an order's life,
  so a viewer can tell free, to pickup and loaded, and nothing about charging or breakdowns.
  Version 2 adds robot events and battery levels (see "Robot events and battery readings" below).
- **Preset maps and generated scenarios only, through the service.** `amr-sim --trace` records
  scenario files too; the service does not accept them yet.
- **The conflict list is capped** at the earliest 1,000 per trace. The total is exact.
- **A run cut short records visits after its end.** A free-flow drive is recorded when it starts.
  Only the Python API can cut a run short (`max_tick`); the service and the command line cannot.
- **Sizes and times were measured on one laptop,** for greedy dispatch on the large map
  ([algorithms](algorithms.md), section 17).

## Robot events and battery readings (digital twin, phase 2a)

- **No place on an edge during a delay or a breakdown.** The model adds time to a corridor
  traversal and does not say where the robot loses it. The events are at the corridor entry, the
  status covers the whole traversal, and the position drawn along that edge is a convention (even
  movement), marked as not modelled. No stopping place is invented (D35).
- **No battery curve while driving.** A level is recorded on arrival at a node, where the model
  charges the edge's energy. In between, the last reading is shown with its age. Nothing is
  interpolated.
- **On a charger, the level is calculated, not read.** The viewer applies the engine's own charging
  formula and labels the value as a calculation. The engine records a reading only when charging
  starts and stops, and when it checks the robot's service state.
- **Free flow has no robot events.** Runs without traffic management have no batteries, no
  breakdowns and no waiting, so there is nothing to record.
- **Events say what, not why.** A replan or a move out of the way is recorded, but not which other
  robot caused it. A wait for a charger does not say which charger it is waiting for.
- **Service state changes at decisions.** A robot goes out of service when a dispatch decision
  finds it low, not the instant its level crosses the threshold. The event is where the simulator
  acts, which is the model's own definition.
- **Traces of disturbed runs are larger:** 40% to 53% more when compressed, measured on six
  scenarios; plain traffic runs are unchanged ([algorithms](algorithms.md), section 19).
- **The cost was measured on one laptop.** Simulation time changed by 0.2% to 3.2%, which is at the
  limit of what the measurement resolves.
- **Fixture coverage.** The committed fixtures cover every status except waiting for a charger,
  which a hand-built case covers in the simulator's tests.

## The viewer (digital twin, phase 1b)

- **A replay, not a live system.** The viewer plays back runs that have finished. It does not show a
  simulation while it runs, and it controls nothing. "Digital twin" means a faithful replay of a
  simulated fleet, not a mirror of a physical one.
- **A graph, not a floor plan.** The map is drawn as points and lines, because that is the model.
  Aisle widths, robot footprints and clearances are not drawn, and nothing on screen says anything
  about physical collisions.
- **Statuses come from events, and only from events.** With a version 2 trace the viewer shows
  charging, out of service, waiting for a charger, breakdown and delay traversals, and waits at the
  end of a corridor. With a version 1 trace it shows none of them and says that they are not
  recorded. It never derives a status from a position: a robot standing on a charger node is
  "charging" only between a charging-started and a charging-stopped event.
- **A waiting robot is drawn short of the node.** A robot at the end of a corridor is drawn a
  little before the node on the edge it came by, so that it does not cover the robot in the node.
  How far before is a drawing convention; the model has no length for it.
- **Up to three status badges per robot** are drawn on the map. The robot panel lists all of them.
- **"Ahead" is a recording.** The dashed path in front of a selected robot is what it did next in
  the finished run, not a plan or a prediction.
- **Generated scenarios only.** The form makes runs on the preset maps. Scenario files with your own
  map play back only as trace files written by `amr-sim --trace`.
- **One run at a time.** There is no side-by-side comparison of two runs yet, and no statistics
  over several seeds. One run is an example, not evidence that one policy beats another: the
  [benchmarks](benchmarks.md) are the evidence.
- **Lists are cut off.** The order list shows the first 150 orders in a state, and the conflict
  list the first 150 conflicts; the map and the timeline mark every listed conflict.
- **Tested in one browser.** The interface was checked by hand in Chrome on Windows. Other
  browsers, touch screens and screen readers were not tested.
- **No automated tests of the interface itself.** The logic it uses (trace reading, interpolation,
  clock, form, view maths) has unit tests; components are checked by hand
  ([plan](DIGITAL_TWIN_PLAN.md)).
- **Local only.** The development server and the service listen on 127.0.0.1, without
  authentication. There is no production build served by the service, and the container image
  does not include the viewer.
- **Performance was measured once:** in Chrome on the development laptop, on the largest run the
  service accepts ([algorithms](algorithms.md), section 18).

## Layouts from floor plans (Phase 3)

- **A layout is a grid approximation of a building:** square cells of one size, axis-aligned, with
  no curves or diagonal racks. The cell size is also the closest two robots can be (D23, D38).
- **The vision step is measured on clean synthetic plans only.** Three of three were transcribed
  exactly, but those plans are pixel-exact drawings that the transcribing agents decoded from pixel
  values. Scanned, rotated, noisy or hand-drawn plans were not tried ([benchmarks](benchmarks.md),
  D42). A transcription is a reading of a picture, to be checked by rendering and confirmed by a
  person.

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
  the project's toolchain. The recovery chart uses three steps of the palette's blue ramp, within the
  limits it documents for ordered series; that was not validated by the script either.
