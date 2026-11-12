import { describe, expect, it } from "vitest";

import { traceJson } from "./fixtures.test-util";
import { poseAt } from "./motion";
import {
  ORDER_STATES,
  type OrderLife,
  buildReplay,
  conflictsAt,
  corridorsOf,
  drivenMetres,
  neighbourTick,
  orderCounts,
  orderStateAt,
} from "./replay";
import { type TraceNode, parseTrace } from "./trace";

const freeFlow = buildReplay(parseTrace(traceJson("free_flow")));
const cutShort = buildReplay(parseTrace(traceJson("cut_short")));
const disturbed = buildReplay(parseTrace(traceJson("disturbed")));

describe("orders", () => {
  it("go through their logged steps in order", () => {
    for (const order of freeFlow.orders) {
      const steps = [
        order.released,
        order.assigned,
        order.pickupReached,
        order.loaded,
        order.dropoffReached,
        order.delivered,
      ];
      expect(steps.every((step) => step !== null)).toBe(true);
      expect(steps).toEqual([...(steps as number[])].sort((a, b) => a - b));
      expect(order.robot).not.toBeNull();
    }
  });

  it("have the state that begins at each logged event", () => {
    const order: OrderLife = {
      id: "O1",
      pickup: "A",
      dropoff: "B",
      releaseTick: 10,
      released: 10,
      assigned: 25,
      pickupReached: 60,
      loaded: 160,
      dropoffReached: 300,
      delivered: 400,
      robot: "R1",
      emptyM: 3,
      loadedM: 14,
    };
    const at = (tick: number) => orderStateAt(order, tick);
    expect([9.9, 10, 24.9, 25, 60, 159.9, 160, 300, 399.9, 400, 1e6].map(at)).toEqual([
      "unreleased",
      "pending",
      "pending",
      "to_pickup",
      "loading",
      "loading",
      "carrying",
      "unloading",
      "unloading",
      "delivered",
      "delivered",
    ]);
  });

  it("are counted once each, at every moment", () => {
    for (const replay of [freeFlow, cutShort, disturbed]) {
      for (const tick of [0, replay.endTick / 3, replay.endTick / 2, replay.endTick]) {
        const counts = orderCounts(replay.orders, tick);
        const total = ORDER_STATES.reduce((sum, state) => sum + counts[state], 0);
        expect(total).toBe(replay.orders.length);
      }
    }
  });

  it("end as the trace's outcome says", () => {
    for (const replay of [freeFlow, cutShort, disturbed]) {
      const counts = orderCounts(replay.orders, replay.endTick);
      const outcome = replay.trace.outcome;
      expect(counts.delivered).toBe(outcome.delivered.length);
      expect(counts.delivered).toBe(replay.trace.kpis["delivered"]);
      expect(counts.unreleased).toBe(outcome.unreleased.length);
      expect(counts.pending).toBe(outcome.pending.length);
      expect(counts.to_pickup + counts.loading + counts.carrying + counts.unloading).toBe(
        outcome.in_progress.length,
      );
    }
    // The run that was cut short really ends with work in progress and orders never released.
    expect(cutShort.trace.outcome.in_progress.length).toBeGreaterThan(0);
    expect(cutShort.trace.outcome.unreleased.length).toBeGreaterThan(0);
  });

  it("carry the planned distances that the KPIs add up", () => {
    for (const replay of [freeFlow, disturbed]) {
      const empty = replay.orders.reduce((sum, order) => sum + order.emptyM, 0);
      const loaded = replay.orders.reduce((sum, order) => sum + order.loadedM, 0);
      expect(empty).toBeCloseTo(replay.trace.kpis["empty_distance_m"] as number, 6);
      expect(loaded).toBeCloseTo(replay.trace.kpis["loaded_distance_m"] as number, 6);
    }
  });
});

describe("corridors", () => {
  const node = (id: string, x: number): TraceNode => ({ id, x, y: 0, kind: "intersection" });
  const nodes = new Map([node("A", 0), node("B", 1), node("C", 2)].map((n) => [n.id, n]));

  it("draw a pair of opposite edges once, and a single edge as one-way", () => {
    const corridors = corridorsOf(nodes, [
      { source: "A", target: "B", length_m: 1 },
      { source: "B", target: "A", length_m: 1 },
      { source: "C", target: "B", length_m: 1 },
    ]);
    expect(corridors.map((c) => [c.a.id, c.b.id, c.oneWay])).toEqual([
      ["A", "B", false],
      ["C", "B", true],
    ]);
  });

  it("cover every directed edge of a real map", () => {
    const edges = freeFlow.trace.map.edges;
    const oneWay = freeFlow.corridors.filter((c) => c.oneWay).length;
    const twoWay = freeFlow.corridors.length - oneWay;
    expect(oneWay + 2 * twoWay).toBe(edges.length);
    expect(oneWay).toBeGreaterThan(0);
  });
});

describe("conflicts", () => {
  it("are those of the trace, and are found at the ticks they cover", () => {
    expect(freeFlow.conflicts.length).toBe(freeFlow.trace.conflicts.total);
    expect(freeFlow.conflicts.length).toBeGreaterThan(0);
    for (const conflict of freeFlow.conflicts) {
      expect(conflictsAt(freeFlow.conflicts, conflict.start)).toContain(conflict);
      expect(conflictsAt(freeFlow.conflicts, conflict.end)).toContain(conflict);
      expect(conflictsAt(freeFlow.conflicts, conflict.end + 0.5)).not.toContain(conflict);
    }
    expect(disturbed.conflicts).toEqual([]);
  });

  it("put both robots of a vertex conflict on the same node", () => {
    const vertex = freeFlow.conflicts.filter((conflict) => conflict.kind === "vertex");
    expect(vertex.length).toBeGreaterThan(0);
    for (const conflict of vertex) {
      const node = freeFlow.nodes.get(conflict.resource[0] as string) as TraceNode;
      for (const robot of conflict.robots) {
        const track = freeFlow.trackById.get(robot);
        expect(track).toBeDefined();
        if (track === undefined) continue;
        const pose = poseAt(track, conflict.start);
        expect([pose.x, pose.y]).toEqual([node.x, node.y]);
      }
    }
  });
});

describe("neighbourTick", () => {
  it("steps to the next and the previous event", () => {
    const ticks = [10, 20, 35];
    expect(neighbourTick(ticks, 0, 1)).toBe(10);
    expect(neighbourTick(ticks, 10, 1)).toBe(20);
    expect(neighbourTick(ticks, 12.5, 1)).toBe(20);
    expect(neighbourTick(ticks, 35, 1)).toBeNull();
    expect(neighbourTick(ticks, 35, -1)).toBe(20);
    expect(neighbourTick(ticks, 20.5, -1)).toBe(20);
    expect(neighbourTick(ticks, 10, -1)).toBeNull();
  });

  it("has every event tick of a trace once, in order", () => {
    const ticks = disturbed.eventTicks;
    expect(ticks).toEqual([...new Set(disturbed.trace.events.map((e) => e.tick))]);
    expect(ticks).toEqual([...ticks].sort((a, b) => a - b));
  });
});

describe("drivenMetres", () => {
  it("adds up the recorded edges, and grows along an edge", () => {
    const track = freeFlow.tracks[0];
    expect(track).toBeDefined();
    if (track === undefined) return;
    const last = track.nodes.length - 1;
    const lengths = new Map(
      freeFlow.trace.map.edges.map((e) => [`${e.source}>${e.target}`, e.length_m]),
    );
    let total = 0;
    for (let i = 1; i <= last; i++) total += lengths.get(`${track.nodes[i - 1]}>${track.nodes[i]}`) ?? NaN;
    expect(drivenMetres(freeFlow, track.id, last, 0)).toBeCloseTo(total, 9);
    expect(drivenMetres(freeFlow, track.id, 0, 0)).toBe(0);
    const half = drivenMetres(freeFlow, track.id, 0, 0.5);
    expect(half).toBeCloseTo((lengths.get(`${track.nodes[0]}>${track.nodes[1]}`) ?? NaN) / 2, 9);
    expect(drivenMetres(freeFlow, "nobody", 0, 0)).toBe(0);
  });
});
