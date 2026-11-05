# Project overview

## Purpose

This is an engineering portfolio project. It demonstrates algorithm and software engineering for
autonomous mobile robot (AMR) fleet management:
- dispatching transport orders to robots;
- planning routes;
- coordinating traffic in shared corridors;
- scheduling battery charging;
- replanning when conditions change.

Everything is evaluated in a reproducible simulation on synthetic data.

## The problem

- **Warehouse.** A directed graph of navigable locations (intersections, storage locations, stations,
  chargers, parking) and the connections between them. Some aisles are one-way, and shared corridors and
  intersections have limited capacity.
- **Fleet.** The target scenario has 25 robots. Each robot has a position, a state, a speed, a battery
  level and an availability. The fleet starts homogeneous; heterogeneous fleets may come later.
- **Orders.** Transport orders with a pickup location, a drop-off location, a release time and a
  priority, and optionally a deadline. They arrive over time, and representative experiments use at
  least 100 of them.
- **Decisions.**
  - Which robot serves which order (assignment), and in what sequence (scheduling).
  - Along which route (routing).
  - How robots share corridors and intersections without conflicts or deadlocks (traffic).
  - When to charge (energy).
  - How to react to new orders, faults and blocked aisles (replanning).

## Approach

- **Small, validated increments.** Each increment starts from tiny examples whose answers can be checked
  by hand, then scales up to the target scenario.
- **Planners are pure functions** from a state snapshot to a decision. The simulator owns time and state
  and checks invariants after every event. For example, a robot can't teleport, and a shared zone's
  occupancy never exceeds its capacity.
- **Baselines before optimization.**
  - Greedy dispatch first.
  - Then the optimal one-shot assignment (Hungarian algorithm).
  - Then bounded-horizon CP-SAT models, used only where they are tractable and add measurable value.
- **Decomposition for tractability.** Assignment, scheduling and routing are separate problems, rather
  than one monolithic model.
- **Hard constraints.** Safety and feasibility are constraints, not low-weight objective terms.
- **Explicit objectives.** Objectives are documented, normalized and introduced one at a time; there
  are no arbitrary weights.

## Evaluation

- Every algorithm is compared on identical seeded scenarios, with repeated runs and several problem sizes
  (3–5, 10 and 25 robots).
- **KPIs:** throughput, waiting and completion time (mean and P95), travel distance, robot and charger
  utilization, congestion waiting, deadlocks, completion rate, and algorithm runtime and latency.
- Results are reported as measured, including when a more sophisticated method brings no improvement.

## Constraints and non-goals

- Synthetic maps, orders and operating data only. No proprietary vendor or customer implementations or
  specifications are reproduced.
- The engine does not control physical robots, and it is not a certified safety system. It models
  feasibility at the level of the graph and the schedule.
- No infrastructure is added (message brokers, Kubernetes, distributed services) before there is a
  demonstrated need for it.

## Status (2026-11-05)

Milestones M1 to M5 are complete:
- routing;
- simulation;
- dispatch with greedy, Hungarian, insertion and CP-SAT;
- traffic with reservations and safe-interval path planning;
- scaling benchmarks with 3 to 25 robots, and figures.

Phase 2 added:
- delays during execution, survived by keeping the planned order of robots (P1);
- batteries, shared chargers and three charging policies, with charger utilization (P2).

Some goals above are **not built yet** and are in the Phase 2 backlog:
- faults, blocked aisles and replanning during execution;
- order priorities and deadlines;
- mutex zones;
- an API service.

What exists is summarized in the [README](../README.md); its limits are listed in
[LIMITATIONS.md](LIMITATIONS.md). See [ROADMAP.md](ROADMAP.md) for milestones and
[ARCHITECTURE.md](ARCHITECTURE.md) for the current design.
