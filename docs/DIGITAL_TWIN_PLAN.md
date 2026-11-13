# Digital twin plan

This document plans the browser-based replay viewer and experiment lab built on top of the engine.
It records what is built, what is planned, and the rules every phase follows. The decisions behind
it are D33 and D34 in [DECISIONS.md](DECISIONS.md).

**Scope, stated once.** Everything is synthetic. The viewer replays finished runs of the Python
simulator; it controls no robot and is not a safety system. "Digital twin" here means a faithful
replay of a simulated fleet, not a live mirror of a physical one. "Optimization Lab" means
algorithmic optimization (assignment, CP-SAT, path planning); nothing here is machine learning.

## The one rule

**The Python simulation is the single source of truth.** The browser never simulates, never plans
and never invents a value.

- Robot positions come from the recorded trajectories (timed paths), by the interpolation that
  `viz/motion.py` defines.
- Robot activities (free, to pickup, loaded) come from the event log.
- KPIs come from `metrics/`, computed in Python from the same run.
- Conflicts come from the independent conflict checker (`traffic/conflicts.py`), never from two
  drawings overlapping on screen.
- What the simulator does not record is not shown. Since phase 2a it records robot events and
  battery readings (D35). It still does not record where on an edge a delayed or broken robot is,
  or a battery level between two nodes, so the viewer shows neither.

## Baseline (2026-11-11, commit `a4e156f`)

The four checks passed before any change: 1569 tests, ruff check, ruff format, mypy strict.

What the audit found:

| Gap | Where |
|---|---|
| `GET /maps/{name}` returned nodes without edges | `api/app.py` |
| A run's trajectories and event log were dropped in the worker process; only KPIs came back | `api/work.py` |
| A `SimulationResult` cannot be pickled (it holds a read-only mapping), so it cannot leave the worker as it is | `sim/simulator.py` |
| The event log has six kinds, all steps of an order's life; nothing marks charging or breakdowns | `sim/log.py` |
| No browser frontend | — |

## Measured trace sizes

Greedy dispatch with traffic, seed 0, measured on the development machine. A trace stores each
robot's visits (node, arrival tick, departure tick), not one position per frame.

| Run | Visits | Log entries | JSON | gzip | Simulation time |
|---|---:|---:|---:|---:|---:|
| small map, 3 robots, 10 min | 212 | 66 | 0.01 MB | under 0.01 MB | 0.0 s |
| large map, 5 robots, 1 h | 6,782 | 864 | 0.19 MB | 0.05 MB | 0.3 s |
| large map, 10 robots, 1 h | 12,919 | 1,686 | 0.37 MB | 0.10 MB | 0.7 s |
| large map, 25 robots, 1 h | 39,776 | 4,638 | 1.12 MB | 0.31 MB | 2.3 s |
| large map, 50 robots, 2 h (the API's limits) | 87,197 | 11,910 | 2.55 MB | 0.69 MB | 7.6 s |

These sizes are from the audit, for visits and log entries only. Complete traces, measured after
the format was built, are larger because events and the map are written as named fields:

| Run | Complete trace, JSON | gzip | Building the trace | Writing and compressing |
|---|---:|---:|---:|---:|
| large map, 10 robots, 1 h | 0.54 MB | 0.11 MB | 0.08 s | 0.12 s |
| large map, 25 robots, 1 h | 1.48 MB | 0.33 MB | 0.45 s | 0.24 s |
| large map, 50 robots, 2 h (the API's limits) | 3.38 MB | 0.73 MB | 1.41 s | 0.77 s |
| the same in free flow (15,078 conflicts, 1,000 listed) | 3.20 MB | 0.65 MB | 1.21 s | 0.50 s |

**Conclusion:** a whole trace fits in one response. Pagination and streaming are not built, because
nothing measured needs them.

## Architecture

```text
POST /simulations {..., "record_trace": true}
  worker process:  simulate() -> SimulationResult
                     -> KPIs (as before)
                     -> replay.build_trace() -> deterministic JSON -> gzip bytes
  server:          keeps the bytes of the most recent traces, bounded by count and by total size
GET /simulations/{id}         unchanged: status and KPIs
GET /simulations/{id}/trace   the stored trace
GET /maps/{name}              nodes, and now edges

amr-sim --trace FILE          the same trace, written by the command line

web/ (Phase 1, second half)   React + TypeScript + Vite; the map and the robots on a 2D canvas
  positionAt / activityAt     ports of viz/motion.py, tested against fixtures produced by Python
```

- **`replay/` is a core package.** Frozen dataclasses, no Pydantic. It reads a `SimulationResult`
  and sits below `api`, so the command line can write traces too. The simulator does not know it
  exists: building a trace cannot change a run.
- **The frontend is a viewer of finished runs.** It plays a trace back; it is not a live fleet
  manager.

## The trace format (version 1)

The format is defined in `src/amr_optimizer/replay/trace.py`, with a JSON Schema in
`src/amr_optimizer/replay/schema.py`. In short:

| Part | Content |
|---|---|
| Header | `format`, `schema_version`, a disclosure that the data is synthetic, and `run`: the settings that produced the run |
| Model | `policy`, `traffic`, `battery`, `tick_s`, `end_tick`, `speed_mps`, `load_ticks`, `unload_ticks` |
| `map` | Nodes (id, x and y in metres, kind) and directed edges (source, target, length in metres) |
| `robots` | Each robot's visits as `[node, arrive_tick, depart_tick]`. The first visit is where it starts |
| `orders` | Id, pickup, drop-off, release tick |
| `events` | The event log: tick, kind, order, node, robot, distance |
| `outcome` | Order ids by state at the end: delivered, in progress, pending, unreleased |
| `counters` | Run totals: breakdowns, repair ticks, replans, moves out of the way, deferred assignments |
| `kpis`, `charging` | The KPIs of `metrics/`; undefined values are null |
| `conflicts` | The total found by the independent checker, and the earliest of them (at most 1,000) |

Rules:

- **Times are integer ticks** (D6). `tick_s` converts them to seconds. Only KPIs are in seconds,
  and their names end in `_s`.
- **A hold has no departure.** Inside the engine, a robot that stays at its last node has the
  departure tick `FOREVER` (2^62). In a trace that departure is `null`. Runs that finish have none:
  the simulator ends every trajectory at the last tick of the run.
- **Deterministic.** The same run gives the same JSON, byte for byte: fixed key order, every real
  number written as a float, no wall-clock values. The gzip header has no timestamp, so on one
  machine the compressed bytes are the same too; another zlib build may compress differently.
  Wall-clock measurements (decision latency, compute time) stay out of the trace and are reported
  by the job, separately.
- **A run plays from tick 0 to `end_tick`.** A run cut short in Python (`max_tick`) can record
  visits after its end, because a free-flow drive is recorded when it starts.
- **Strict.** `trace_from_dict` rejects unknown, missing and mistyped fields, and then checks that
  the parts fit: every visit is on the map, every step drives an existing edge, events are in time
  order and name known robots and orders.

## Phases

| Phase | Content | Status |
|---|---|---|
| **1a** Backend contract | Trace package, map edges, trace over HTTP, `amr-sim --trace`, golden fixtures | **Done** (2026-11-11), see the [roadmap](ROADMAP.md) |
| **1b** Viewer | `web/`: map, animated robots, run form, playback controls, robot and order details, KPIs, conflicts | **Done** (2026-11-12), see below |
| **2a** Robot observability | Simulator events for charging, service state, delays, breakdowns and waits; battery readings; trace version 2; the viewer shows them with their source (D35) | **Done** (2026-11-13), see the [roadmap](ROADMAP.md) |
| **2b** Experiment lab | Paired comparisons over seeds with intervals, on disturbances that are the same for every policy | Proposed: [EXPERIMENT_LAB_DESIGN.md](EXPERIMENT_LAB_DESIGN.md) |
| **3** Scenario exploration | Custom scenarios and maps through the API, with the existing strict parsers as the authority | Planned |
| **4** One measured optimizer upgrade | First candidate: traffic-aware cost estimates in dispatch, against the existing policies on paired seeds | Planned |
| **5** Polish | Large-map rendering measurements, accessibility, keyboard controls, recorded demo | Planned |

## What phase 1a delivered

| Piece | Where |
|---|---|
| The trace, its builder, strict parser and gzip writer | `src/amr_optimizer/replay/trace.py` |
| The format as a JSON Schema | `src/amr_optimizer/replay/schema.py` |
| `record_trace`, `GET /simulations/{id}/trace`, bounded storage | `src/amr_optimizer/api/` (`app.py`, `work.py`, `traces.py`, `models.py`) |
| Directed edges in `GET /maps/{name}` | `src/amr_optimizer/api/app.py` |
| `amr-sim --trace FILE` | `src/amr_optimizer/cli.py` |
| Golden fixtures and the script that writes them | `tests/data/replay/`, `scripts/make_replay_fixtures.py` |

**Golden fixtures.** Three real runs on the small map, each with its trace and with samples of
every robot's position and activity from the Python reference functions:

| Fixture | Run | What it covers |
|---|---|---|
| `free_flow` | 3 robots, greedy, no traffic | Drives without waiting, 8 conflicts |
| `cut_short` | The same run stopped at tick 1500 | Orders in progress and unreleased; visits after the end |
| `disturbed` | 3 robots, `insertion-fair`, traffic, delays, charging, breakdowns, replanning | Long waits, 20 breakdowns, 9 replans, no conflicts |

## What phase 1b delivered

The viewer, in `web/`. Start it with `.\scripts\dev.ps1`; its own notes are in `web/README.md`.

| Piece | Where |
|---|---|
| Reading and checking a trace | `web/src/lib/trace.ts` |
| Position and activity at any moment (the port of `viz/motion.py`) | `web/src/lib/motion.ts` |
| Playback clock, kept within the run | `web/src/lib/playback.ts` |
| Orders by logged step, corridors, conflicts at a moment | `web/src/lib/replay.ts` |
| The service's endpoints; the form's limits from its OpenAPI document | `web/src/lib/api.ts`, `web/src/lib/runForm.ts` |
| Map drawing and picking on a 2D canvas | `web/src/render/scene.ts`, `web/src/lib/view.ts` |
| Map, playback bar, run panel, details | `web/src/components/` |
| One command to start both parts | `scripts/dev.ps1` |
| CI job: lint, type check, tests, build, on Windows and Linux | `.github/workflows/ci.yml` |

**What was checked in a real browser** (Chrome, against the running service), on 2026-11-12:

| Check | Result |
|---|---|
| Map explorer before any run | The three preset maps draw with every directed edge; one-way connections carry arrows |
| A run from the form | Each of the four presets ran on the service and played back |
| Playback | Play, pause, speed, scrub, steps of 1 s, 10 s and one tick, jump to the next logged event; the playhead stops at the end and playing there starts again from 0 |
| A recorded path against the raw trace | A detour into a storage spur that looked odd on screen was in the trace: the robot waited there for 5.6 s |
| Conflicts | A free-flow run showed the 85 conflicts its trace lists, on the map, on the timeline and in the list |
| Trace states | Pending while running; a trace dropped by the service (after 22 more runs: 20 kept, 5 dropped) gave the 410 explanation and "Run again" |
| Trace files | A valid file played without the service; a file of another version and a file that is not JSON were refused with the reason |
| Service down | Clear notice with the command to start it; "Try again" reconnected |
| gzip | The browser received `Content-Encoding: gzip` and decompressed it itself |
| Narrow windows | Checked at 1000 px (two columns, details below) and 540 px (one column) |
| The largest run the service accepts (50 robots, 995 orders, 621 breakdowns) | Loaded and checked in 0.4 s; at 60x speed, frames took 10 ms at the median and 20 ms at the 95th percentile |

**Not checked:** other browsers than Chrome, touch input on a real touch screen, and screen
readers. The interface has no automated tests of its own; the logic underneath has 116.

## A three-minute demonstration

1. **Start.** Run `.\scripts\dev.ps1`. The viewer opens on the large map. Point out the notice
   in the header: a synthetic simulation, no real robot.
2. **The map (30 s).** Zoom into an aisle with the mouse wheel. One-way aisles carry arrows;
   storage places sit on dead-end spurs. Click a node: the Map tab lists where robots can drive
   from it. Press F to fit.
3. **A run (30 s).** Click the preset "Fleet with traffic", then "Run simulation". The engine
   simulates 10 robots under the CP-SAT dispatcher and records the run; it takes a few seconds.
4. **Playback (45 s).** Press Space. Set the speed to 60x, then drag the timeline. Robots are
   coloured by activity: free, to pickup, loaded. The numbers at the pickups are waiting loads.
5. **A robot (30 s).** Click a loaded robot. Its panel shows the edge it is driving and its order;
   the map shows its path of the last minute and the recorded path to its drop-off. Click the order
   id: the six logged steps of the order, each with its time; click a step to jump there.
6. **The evidence (30 s).** Open Overview: every order delivered, completion times, utilization,
   and 0 conflicts, found by the engine's independent checker.
7. **The contrast (15 s).** Click the preset "Free flow" and run it. The Conflicts tab now lists
   what traffic management prevents; click one to see two robots in the same place.

## Phase 1 acceptance criteria

- The four Python checks pass at every commit, and every test that existed before still passes
  unchanged.
- `GET /simulations/{id}` and `GET /maps/{name}` keep every field they had.
- The trace of a seeded run equals the direct run: trajectories, event log and KPIs.
- Building a trace changes nothing in the run it describes.
- The TypeScript interpolation matches `position_at()` on the golden fixtures, at arrivals,
  departures, between nodes, at fractional ticks and after the end.
- Every robot, position and number on screen comes from a trace or a job result.
- One documented PowerShell command starts the demo, and the synthetic-data notice is always
  visible.

## Extension points, not built

The trace is versioned so that later phases can add to it without breaking viewers:

- **More event kinds** (charging started, breakdown, repaired) once the simulator records them.
- **Battery levels over time**, recorded at the source, not interpolated in the browser.
- **Mutex zones and closed corridors** as map-level resources, with the conflict checker extended
  to the same resources.
- **Comparison runs**: several traces of the same scenario under different policies.
