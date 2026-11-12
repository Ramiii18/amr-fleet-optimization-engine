import { describe, expect, it } from "vitest";

import { FIXTURE_NAMES, traceJson } from "./fixtures.test-util";
import { TraceFormatError, parseTrace } from "./trace";

type Json = Record<string, any>; // eslint-disable-line @typescript-eslint/no-explicit-any

function broken(change: (data: Json) => void): Json {
  const data = structuredClone(traceJson("free_flow")) as Json;
  change(data);
  return data;
}

describe("parseTrace", () => {
  it.each(FIXTURE_NAMES)("accepts the fixture %s and changes nothing in it", (name) => {
    const data = traceJson(name);
    expect(parseTrace(data)).toEqual(data);
  });

  it("checks the version before anything else", () => {
    // A later version may differ anywhere, so even an otherwise empty object is refused for
    // its version, not for what it lacks.
    expect(() => parseTrace({ format: "amr-replay-trace", schema_version: 2 })).toThrow(
      /version 2 is not supported; this viewer reads version 1/,
    );
    expect(() => parseTrace(broken((d) => (d.schema_version = "1")))).toThrow(/not supported/);
  });

  it("refuses what is not a replay trace at all", () => {
    for (const data of [null, [], "trace", 3, {}]) {
      expect(() => parseTrace(data)).toThrow(TraceFormatError);
    }
    expect(() => parseTrace({ format: "amr-scenario", version: 1 })).toThrow(
      /this is not a replay trace/,
    );
  });

  it.each<[string, (data: Json) => void, RegExp]>([
    ["an unknown field", (d) => (d.colour = "red"), /trace: unknown field\(s\) colour/],
    ["a missing field", (d) => delete d.events, /trace: missing field\(s\) events/],
    ["a tick with a fraction", (d) => (d.end_tick = 10.5), /trace.end_tick: expected a non-negative integer/],
    ["a tick too large for a number", (d) => (d.end_tick = 2 ** 62), /trace.end_tick/],
    ["a flag that is a number", (d) => (d.traffic = 1), /trace.traffic: expected true or false/],
    ["a tick length of zero", (d) => (d.tick_s = 0), /tick_s: must be positive/],
    ["an unknown node kind", (d) => (d.map.nodes[0].kind = "lift"), /map.nodes\[0\].kind/],
    ["a node without a position", (d) => (d.map.nodes[0].x = null), /map.nodes\[0\].x: expected a number/],
    ["an edge to nowhere", (d) => (d.map.edges[0].target = "NOWHERE"), /map.edges\[0\].target: unknown node/],
    ["no robots", (d) => (d.robots = []), /at least one robot/],
    ["a visit without a departure field", (d) => (d.robots[0].visits[0] = ["PK00", 0]), /robots\[0\].visits\[0\]: expected \[node, arrive, depart\]/],
    ["a visit off the map", (d) => (d.robots[0].visits[1][0] = "NOWHERE"), /robots\[0\].visits\[1\]\[0\]: unknown node/],
    ["a robot that starts late", (d) => (d.robots[0].visits[0][1] = 1), /must start at tick 0/],
    ["a hold in the middle of a path", (d) => (d.robots[0].visits[0][2] = null), /only the last visit can be a hold/],
    ["an arrival before the previous departure", (d) => (d.robots[0].visits[1][1] = 0), /arrival must come after/],
    [
      "a step without an edge",
      (d) => {
        const visits = d.robots[0].visits;
        const near = new Set(
          d.map.edges.filter((e: Json) => e.source === visits[0][0]).map((e: Json) => e.target),
        );
        visits[1][0] = d.map.nodes.find((n: Json) => !near.has(n.id) && n.id !== visits[0][0]).id;
      },
      /no edge/,
    ],
    ["two robots with one id", (d) => (d.robots[1].id = d.robots[0].id), /duplicate robot id/],
    ["an unknown event kind", (d) => (d.events[0].kind = "lost"), /events\[0\].kind/],
    ["an event after the end", (d) => (d.events.at(-1).tick = 10 ** 9), /outside the run/],
    ["an event of an unknown order", (d) => (d.events[0].order = "O?"), /unknown order/],
    ["a release by a robot", (d) => (d.events[0].robot = d.robots[0].id), /except a release/],
    ["an order with one place", (d) => (d.orders[0].dropoff = null), /orders\[0\].dropoff: expected a string/],
    ["a negative counter", (d) => (d.counters.replans = -1), /counters.replans/],
    ["a KPI that is a list", (d) => (d.kpis.delivered = [1]), /kpis.delivered/],
    ["fewer conflicts counted than listed", (d) => (d.conflicts.total = 0), /more are listed than counted/],
    ["a conflict of an unknown kind", (d) => (d.conflicts.listed[0].kind = "near miss"), /conflicts.listed\[0\].kind/],
    ["a conflict of an unknown robot", (d) => (d.conflicts.listed[0].robots = ["R01", "R99"]), /unknown robot/],
  ])("refuses %s", (_name, change, reason) => {
    const data = broken(change);
    expect(() => parseTrace(data)).toThrow(TraceFormatError);
    expect(() => parseTrace(data)).toThrow(reason);
  });

  it("reads a hold as a null departure", () => {
    const data = broken((d) => {
      const visits = d.robots[0].visits;
      visits[visits.length - 1][2] = null;
    });
    expect(parseTrace(data).robots[0]?.visits.at(-1)?.[2]).toBeNull();
  });
});
