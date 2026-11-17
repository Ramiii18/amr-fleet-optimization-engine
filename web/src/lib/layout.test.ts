import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import { poseAt } from "./motion";
import { buildReplay, orderStateAt } from "./replay";
import { parseTrace } from "./trace";

/** A run on a layout file (Phase 3, D38): one node per cell, ids such as `X-r002-c001`. A Python
 * test re-runs it and requires the same bytes, so this is what the engine writes today. */
const FILE = new URL("../../../tests/data/layout/two_aisles.trace.json", import.meta.url);

function replay() {
  return buildReplay(parseTrace(JSON.parse(readFileSync(fileURLToPath(FILE), "utf8"))));
}

describe("a trace of a layout run", () => {
  it("is read and prepared like any other trace", () => {
    const r = replay();
    expect(r.nodes.size).toBe(30);
    expect(r.nodes.get("X-r002-c001")).toMatchObject({ x: 1, y: 8, kind: "intersection" });
    expect(r.nodes.get("ST-r009-c003")).toMatchObject({ x: 3, y: 1, kind: "station" });
    expect(r.trace.run["map"]).toBe("two_aisles.layout.json");
    // Without the slot: 18 two-way pairs and 12 one-way edges (48 directed edges), 30 corridors.
    expect(r.corridors).toHaveLength(30);
    expect(r.corridors.filter((c) => c.oneWay)).toHaveLength(12);
    expect(r.bounds).toEqual({ minX: 1, minY: 1, maxX: 6, maxY: 9 });
  });

  it("delivers every order and keeps robots on the map", () => {
    const r = replay();
    expect(r.orders).toHaveLength(18);
    expect(r.orders.every((order) => orderStateAt(order, r.endTick) === "delivered")).toBe(true);
    expect(r.conflicts).toHaveLength(0);
    for (const track of r.tracks) {
      for (let tick = 0; tick <= r.endTick; tick += 25) {
        const { x, y } = poseAt(track, tick);
        expect(x).toBeGreaterThanOrEqual(r.bounds.minX);
        expect(x).toBeLessThanOrEqual(r.bounds.maxX);
        expect(y).toBeGreaterThanOrEqual(r.bounds.minY);
        expect(y).toBeLessThanOrEqual(r.bounds.maxY);
      }
    }
  });
});
