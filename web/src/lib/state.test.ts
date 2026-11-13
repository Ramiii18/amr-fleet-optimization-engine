import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import { activityAt, buildTrack, poseAt, positionAt } from "./motion";
import { buildReplay, robotLevelAt, statusCounts } from "./replay";
import { STATUSES, buildStatusTimeline, levelAt, statusIntervals, statusesAt } from "./state";
import { type RobotEvent, type TraceNode, TraceFormatError, parseTrace } from "./trace";

// The version 2 golden fixtures written by Python (`scripts/make_replay_fixtures.py`): real
// runs, with every robot's position, activity, statuses and battery level computed by the
// reference functions of `replay/state.py`. The viewer must reproduce them from the trace alone.
const NAMES = ["free_flow", "cut_short", "disturbed", "charging"] as const;
const FOLDER = new URL("../../../tests/data/replay/v2/", import.meta.url);

type Row = [
  tick: number,
  x: number,
  y: number,
  activity: string,
  status: string[],
  units: number | null,
  asOf: number | null,
  source: string | null,
];

function read(file: string): unknown {
  return JSON.parse(readFileSync(fileURLToPath(new URL(file, FOLDER)), "utf8"));
}

function samples(name: string): Record<string, Row[]> {
  const data = read(`${name}.samples.json`) as { version: number; columns: string[]; robots: Record<string, Row[]> };
  if (data.version !== 2) throw new Error(`${name}: unexpected samples version`);
  expect(data.columns).toEqual([
    "tick",
    "x_m",
    "y_m",
    "activity",
    "status",
    "level_units",
    "level_as_of_tick",
    "level_source",
  ]);
  return data.robots;
}

describe.each(NAMES)("version 2 golden fixture %s", (name) => {
  const data = read(`${name}.trace.json`);
  const replay = buildReplay(parseTrace(data));
  const expected = samples(name);

  it("is read without changing anything in it", () => {
    expect(parseTrace(data)).toEqual(data);
    expect(replay.observed).toBe(true);
  });

  it("gives exactly Python's positions, with waits at the end of corridors", () => {
    let checked = 0;
    for (const track of replay.tracks) {
      for (const [tick, x, y] of expected[track.id] ?? []) {
        expect(positionAt(track, tick), `${track.id} at tick ${tick}`).toEqual([x, y]);
        checked += 1;
      }
    }
    expect(checked).toBeGreaterThan(300);
  });

  it("gives exactly Python's activities and statuses", () => {
    for (const track of replay.tracks) {
      const timeline = replay.statuses.get(track.id);
      for (const [tick, , , activity, status] of expected[track.id] ?? []) {
        expect(activityAt(replay.activity.get(track.id), tick), `${track.id} at ${tick}`).toBe(activity);
        expect(statusesAt(timeline, tick), `${track.id} at tick ${tick}`).toEqual(status);
      }
    }
  });

  it("gives exactly Python's battery levels, with their tick and their source", () => {
    const sources = new Set<string | null>();
    for (const track of replay.tracks) {
      for (const [tick, , , , , units, asOf, source] of expected[track.id] ?? []) {
        const level = robotLevelAt(replay, track.id, tick);
        sources.add(source);
        if (units === null) {
          expect(level, `${track.id} at tick ${tick}`).toBeNull();
        } else {
          expect(level, `${track.id} at tick ${tick}`).toEqual({ units, asOf, source });
          expect(level?.asOf).toBeLessThanOrEqual(tick); // never dated after the moment shown
        }
      }
    }
    if (name === "charging") expect([...sources].sort()).toEqual(["charging_formula", "reading"]);
    if (name === "free_flow" || name === "cut_short") expect([...sources]).toEqual([null]);
  });

  it("counts each robot's statuses once", () => {
    for (const tick of [0, replay.endTick / 3, replay.endTick / 2, replay.endTick]) {
      const counts = statusCounts(replay, tick);
      for (const status of STATUSES) {
        const robots = replay.tracks.filter((track) =>
          statusesAt(replay.statuses.get(track.id), tick).includes(status),
        );
        expect(counts[status]).toBe(robots.length);
      }
    }
  });
});

describe("a robot that waits at the end of a corridor", () => {
  const replay = buildReplay(parseTrace(read("disturbed.trace.json")));
  const waits = replay.trace.robot_events.filter((event) => event.kind === "corridor_wait_started");

  it("stands at the end from the moment it gets there until it enters the node", () => {
    expect(waits.length).toBeGreaterThan(0);
    for (const event of waits) {
      const track = replay.trackById.get(event.robot);
      if (track === undefined) throw new Error("unknown robot");
      const before = poseAt(track, event.tick - 0.5);
      const during = poseAt(track, event.tick);
      const entered = track.arrive[during.visit + 1] as number;
      expect([before.moving, before.waiting]).toEqual([true, false]);
      expect([during.moving, during.waiting, during.share]).toEqual([true, true, 1]);
      expect(poseAt(track, entered - 0.25).waiting).toBe(true);
      expect(poseAt(track, entered)).toMatchObject({ moving: false, waiting: false });
      // At the end of the corridor: the position of the node it will enter.
      expect([during.x, during.y]).toEqual([track.xs[during.visit + 1], track.ys[during.visit + 1]]);
      expect(statusesAt(replay.statuses.get(event.robot), event.tick)).toContain("corridor_wait");
    }
  });

  it("is not held back when the trace has no such event", () => {
    // The same robot without its events: even movement over the whole edge, as in version 1.
    const event = waits[0] as RobotEvent;
    const robot = replay.trace.robots.find((candidate) => candidate.id === event.robot);
    if (robot === undefined) throw new Error("unknown robot");
    const plain = buildTrack(robot, replay.nodes);
    const pose = poseAt(plain, event.tick);
    expect([pose.waiting, pose.share < 1]).toEqual([false, true]);
  });
});

describe("statuses and levels by hand", () => {
  const nodes = new Map<string, TraceNode>([
    ["A", { id: "A", x: 0, y: 0, kind: "parking" }],
    ["B", { id: "B", x: 10, y: 0, kind: "intersection" }],
    ["C", { id: "C", x: 20, y: 0, kind: "charger" }],
  ]);
  // Leaves A at 10; the drive to B (100 ticks) includes a repair of 50: arrives at 160. Leaves B
  // at 160, arrives at the charger C at 260 with 5,000 units and charges 10 units per tick.
  const track = buildTrack(
    {
      id: "R1",
      visits: [
        ["A", 0, 10],
        ["B", 160, 160],
        ["C", 260, 900],
      ],
      levels: [7000, 6000, 5000],
    },
    nodes,
  );
  const model = { capacity: 8000, charge_per_tick: 10, low: 5500, high: 7000, back_in_service: 7000, units_per_metre: 100 };
  const event = (tick: number, kind: RobotEvent["kind"], extra: Partial<RobotEvent> = {}): RobotEvent => ({
    tick,
    kind,
    robot: "R1",
    node: "A",
    target: null,
    ticks: null,
    level: null,
    reason: null,
    ...extra,
  });
  const events = [
    event(10, "breakdown", { target: "B", ticks: 50 }),
    event(260, "charging_started", { node: "C", level: 5000 }),
    event(260, "out_of_service", { node: "C", level: 5000 }),
    event(460, "back_in_service", { node: "C", level: 7000 }),
  ];
  const intervals = statusIntervals(track, events);
  const timeline = buildStatusTimeline(track, events);

  it("lasts a breakdown from the departure until the robot enters the next node", () => {
    expect(intervals[0]).toMatchObject({ status: "breakdown", start: 10, end: 160 });
    expect(statusesAt(timeline, 9.9)).toEqual([]);
    expect(statusesAt(timeline, 10)).toEqual(["breakdown"]);
    expect(statusesAt(timeline, 159.9)).toEqual(["breakdown"]);
    expect(statusesAt(timeline, 160)).toEqual([]);
  });

  it("includes the first tick of an interval and not the last, and keeps an open one open", () => {
    expect(statusesAt(timeline, 260)).toEqual(["charging", "out_of_service"]);
    expect(statusesAt(timeline, 459.9)).toEqual(["charging", "out_of_service"]);
    expect(statusesAt(timeline, 460)).toEqual(["charging"]); // charging never stops in this run
    expect(statusesAt(timeline, 1e6)).toEqual(["charging"]);
    expect(statusesAt(undefined, 10)).toEqual([]);
  });

  it("shows the last reading while driving, and never a value between two readings", () => {
    const at = (tick: number) => levelAt(track, [7000, 6000, 5000], events, model, tick);
    expect(at(0)).toEqual({ units: 7000, asOf: 0, source: "reading" });
    // On the edge A -> B, for 150 ticks: still the level at A. The 1,000 units of the edge are
    // charged on arrival; nothing in between is known, so nothing in between is shown.
    for (const tick of [10, 60, 159.9]) expect(at(tick)).toEqual({ units: 7000, asOf: 0, source: "reading" });
    expect(at(160)).toEqual({ units: 6000, asOf: 160, source: "reading" });
    expect(at(259.9)).toEqual({ units: 6000, asOf: 160, source: "reading" });
  });

  it("uses the charging formula on a charger, says so, and stops at the capacity", () => {
    const at = (tick: number) => levelAt(track, [7000, 6000, 5000], events, model, tick);
    expect(at(260)).toEqual({ units: 5000, asOf: 260, source: "reading" });
    expect(at(260.9)).toEqual({ units: 5000, asOf: 260, source: "reading" });
    expect(at(261)).toEqual({ units: 5010, asOf: 261, source: "charging_formula" });
    expect(at(360.5)).toEqual({ units: 6000, asOf: 360, source: "charging_formula" });
    expect(at(460)).toEqual({ units: 7000, asOf: 460, source: "reading" }); // the event's level
    expect(at(461)).toEqual({ units: 7010, asOf: 461, source: "charging_formula" });
    expect(at(900)).toEqual({ units: 8000, asOf: 900, source: "charging_formula" }); // full
  });
});

describe("version 1 and broken version 2 traces", () => {
  const v1 = JSON.parse(
    readFileSync(fileURLToPath(new URL("../disturbed.trace.json", FOLDER)), "utf8"),
  ) as Record<string, unknown>;

  it("reads a version 1 trace as a run whose robot events are unknown", () => {
    const replay = buildReplay(parseTrace(v1));
    expect(replay.observed).toBe(false);
    expect(replay.trace.robot_events).toEqual([]);
    expect(replay.battery).toBeNull();
    for (const track of replay.tracks) {
      expect(robotLevelAt(replay, track.id, 100)).toBeNull();
      expect(statusesAt(replay.statuses.get(track.id), 100)).toEqual([]);
    }
  });

  type Json = Record<string, any>; // eslint-disable-line @typescript-eslint/no-explicit-any
  const broken = (change: (data: Json) => void): Json => {
    const data = structuredClone(read("charging.trace.json")) as Json;
    change(data);
    return data;
  };
  const firstOf = (data: Json, kind: string): Json => data.robot_events.find((e: Json) => e.kind === kind);

  it.each<[string, (data: Json) => void, RegExp]>([
    ["a later version", (d) => (d.schema_version = 3), /version 3 is not supported; this viewer reads versions 1 and 2/],
    ["a version 2 trace labelled as version 1", (d) => (d.schema_version = 1), /unknown field\(s\) battery_model, robot_events/],
    ["no robot events field", (d) => delete d.robot_events, /missing field\(s\) robot_events/],
    ["a robot without its levels field", (d) => delete d.robots[0].levels, /missing field\(s\) levels/],
    ["one level too few", (d) => d.robots[0].levels.pop(), /one level per visit/],
    ["levels for one robot only", (d) => (d.robots[0].levels = null), /exactly with a battery model/],
    ["a level above the capacity", (d) => (d.robots[0].levels[0] = 10 ** 9), /outside 0 to/],
    ["a battery model of capacity zero", (d) => (d.battery_model.capacity = 0), /must be positive/],
    ["an unknown kind of robot event", (d) => (firstOf(d, "delay").kind = "teleport"), /robot_events\[\d+\]\.kind/],
    ["a delay without its ticks", (d) => (firstOf(d, "delay").ticks = null), /a delay event has the fields target, ticks/],
    ["a delay with a level", (d) => (firstOf(d, "delay").level = 5), /a delay event has the fields/],
    ["charging without a level", (d) => (firstOf(d, "charging_started").level = null), /a charging_started event has the fields level/],
    ["a trip for an unknown reason", (d) => (firstOf(d, "charger_trip_started").reason = "bored"), /must be low or top_up/],
    ["an event of an unknown robot", (d) => (firstOf(d, "delay").robot = "R99"), /unknown robot/],
    ["an event at an unknown node", (d) => (firstOf(d, "delay").target = "NOWHERE"), /unknown node/],
    ["a robot event after the end", (d) => (d.robot_events.at(-1).tick = 10 ** 9), /outside the run/],
  ])("refuses %s", (_name, change, reason) => {
    const data = broken(change);
    expect(() => parseTrace(data)).toThrow(TraceFormatError);
    expect(() => parseTrace(data)).toThrow(reason);
  });
});
