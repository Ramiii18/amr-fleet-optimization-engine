import { describe, expect, it } from "vitest";

import { FIXTURE_NAMES, samples, traceJson } from "./fixtures.test-util";
import {
  type Track,
  activityAt,
  buildActivityChanges,
  buildTrack,
  clampTick,
  countAtOrBefore,
  orderAt,
  poseAt,
  positionAt,
} from "./motion";
import { buildReplay } from "./replay";
import { type TraceNode, parseTrace } from "./trace";

describe.each(FIXTURE_NAMES)("golden fixture %s", (name) => {
  const replay = buildReplay(parseTrace(traceJson(name)));
  const expected = samples(name);

  it("has samples for every robot of the trace", () => {
    expect(Object.keys(expected)).toEqual(replay.tracks.map((track) => track.id));
    for (const rows of Object.values(expected)) expect(rows.length).toBeGreaterThan(50);
  });

  it("gives exactly the positions of Python's position_at", () => {
    let checked = 0;
    let fractional = 0;
    for (const track of replay.tracks) {
      for (const [tick, x, y] of expected[track.id] ?? []) {
        // Exactly equal, not approximately: the same arithmetic on the same doubles.
        expect(positionAt(track, tick), `${track.id} at tick ${tick}`).toEqual([x, y]);
        checked += 1;
        if (!Number.isInteger(tick)) fractional += 1;
      }
    }
    expect(checked).toBeGreaterThan(300);
    expect(fractional).toBeGreaterThan(100);
  });

  it("gives exactly the activities of Python's ActivityTimeline", () => {
    const seen = new Set<string>();
    for (const track of replay.tracks) {
      const changes = replay.activity.get(track.id);
      for (const [tick, , , activity] of expected[track.id] ?? []) {
        expect(activityAt(changes, tick), `${track.id} at tick ${tick}`).toBe(activity);
        seen.add(activity);
      }
    }
    expect([...seen].sort()).toEqual(["free", "loaded", "to pickup"]);
  });

  it("agrees with a plain scan of the visits, the way the reference is written", () => {
    // The reference scans visits in order; `poseAt` uses a binary search. Same answers.
    const scan = (track: Track, tick: number): [number, number] => {
      const n = track.arrive.length;
      if (tick <= (track.arrive[0] as number)) return [track.xs[0] as number, track.ys[0] as number];
      for (let i = 0; i + 1 < n; i++) {
        const depart = track.depart[i] as number;
        const x = track.xs[i] as number;
        const y = track.ys[i] as number;
        if (tick <= depart) return [x, y];
        if (tick < (track.arrive[i + 1] as number)) {
          const share = (tick - depart) / ((track.arrive[i + 1] as number) - depart);
          return [
            x + share * ((track.xs[i + 1] as number) - x),
            y + share * ((track.ys[i + 1] as number) - y),
          ];
        }
      }
      return [track.xs[n - 1] as number, track.ys[n - 1] as number];
    };
    for (const track of replay.tracks) {
      for (let tick = -2; tick <= replay.endTick + 300; tick += 3.7) {
        expect(positionAt(track, tick)).toEqual(scan(track, tick));
      }
    }
  });

  it("keeps a moving robot strictly between the two nodes of its edge", () => {
    for (const track of replay.tracks) {
      for (const [tick] of expected[track.id] ?? []) {
        const pose = poseAt(track, tick);
        expect(pose.share).toBeGreaterThanOrEqual(0);
        expect(pose.share).toBeLessThan(1);
        if (pose.moving) {
          expect(tick).toBeGreaterThan(track.depart[pose.visit] as number);
          expect(tick).toBeLessThan(track.arrive[pose.visit + 1] as number);
        }
      }
    }
  });
});

describe("poseAt on a hand-made path", () => {
  const nodes = new Map<string, TraceNode>([
    ["A", { id: "A", x: 0, y: 0, kind: "parking" }],
    ["B", { id: "B", x: 10, y: 0, kind: "intersection" }],
    ["C", { id: "C", x: 10, y: 4, kind: "station" }],
  ]);
  // Stands at A until tick 20, drives to B in 100 ticks, waits there 30, drives to C in 40.
  const visits = [
    ["A", 0, 20],
    ["B", 120, 150],
    ["C", 190, 250],
  ] as const;
  const track = buildTrack({ id: "R1", visits: visits.map((v) => [...v]), levels: null }, nodes);

  it.each([
    [-5, 0, 0, false],
    [0, 0, 0, false],
    [20, 0, 0, false], // the departure tick itself: still at the node
    [20.5, 0.05, 0, true],
    [70, 5, 0, true],
    [119.9, 9.99, 0, true],
    [120, 10, 0, false], // the arrival tick itself: at the node
    [135, 10, 0, false], // waiting
    [150, 10, 0, false],
    [170, 10, 2, true],
    [190, 10, 4, false],
    [250, 10, 4, false],
    [251, 10, 4, false], // after the last departure it stays at its last node
    [1e9, 10, 4, false],
  ])("at tick %d is at (%d, %d), moving: %s", (tick, x, y, moving) => {
    const pose = poseAt(track, tick);
    expect(pose.x).toBeCloseTo(x, 12);
    expect(pose.y).toBeCloseTo(y, 12);
    expect(pose.moving).toBe(moving);
  });

  it("treats a hold as standing for ever", () => {
    const held = buildTrack(
      {
        id: "R2",
        visits: [
          ["A", 0, 20],
          ["B", 120, null],
        ],
        levels: null,
      },
      nodes,
    );
    expect(held.depart[1]).toBe(Infinity);
    expect(poseAt(held, 5e8)).toMatchObject({ x: 10, y: 0, moving: false, visit: 1 });
  });

  it("refuses a visit that is not on the map", () => {
    expect(() => buildTrack({ id: "R3", visits: [["Z", 0, 1]], levels: null }, nodes)).toThrow("unknown node Z");
  });
});

describe("clampTick", () => {
  it("keeps playback inside the run", () => {
    expect(clampTick(-3, 100)).toBe(0);
    expect(clampTick(0, 100)).toBe(0);
    expect(clampTick(42.5, 100)).toBe(42.5);
    expect(clampTick(100, 100)).toBe(100);
    expect(clampTick(100.1, 100)).toBe(100);
    expect(clampTick(Number.NaN, 100)).toBe(0);
    expect(clampTick(Infinity, 100)).toBe(100);
  });

  it("never plays into the visits a cut-short run recorded after its end", () => {
    const replay = buildReplay(parseTrace(traceJson("cut_short")));
    const beyond = replay.tracks.filter(
      (track) => (track.arrive[track.arrive.length - 1] as number) > replay.endTick,
    );
    expect(beyond.length).toBeGreaterThan(0); // the fixture really has such visits

    for (const track of beyond) {
      const atEnd = positionAt(track, replay.endTick);
      const last = track.arrive.length - 1;
      // Asking for any later moment, clamped, gives the position at the end of the run ...
      for (const tick of [replay.endTick + 0.1, replay.endTick + 500, Infinity]) {
        expect(positionAt(track, clampTick(tick, replay.endTick))).toEqual(atEnd);
      }
      // ... which is not where the recorded path finally ends.
      expect(atEnd).not.toEqual([track.xs[last], track.ys[last]]);
    }
  });
});

describe("countAtOrBefore", () => {
  it("counts like Python's bisect_right", () => {
    const values = [10, 20, 20, 30];
    expect([5, 10, 15, 20, 25, 30, 35].map((x) => countAtOrBefore(values, x))).toEqual([
      0, 1, 1, 3, 3, 4, 4,
    ]);
    expect(countAtOrBefore([], 1)).toBe(0);
  });
});

describe("activities", () => {
  const trace = parseTrace(traceJson("free_flow"));
  const changes = buildActivityChanges(trace);

  it("change at the tick of their event, not before", () => {
    const assigned = trace.events.find((event) => event.kind === "assigned" && event.tick > 0);
    expect(assigned).toBeDefined();
    if (assigned === undefined || assigned.robot === null) return;
    const robot = changes.get(assigned.robot);
    expect(activityAt(robot, assigned.tick)).toBe("to pickup");
    expect(orderAt(robot, assigned.tick)).toBe(assigned.order);
    expect(activityAt(robot, assigned.tick + 0.5)).toBe("to pickup");
  });

  it("are free before the first job, after a delivery, and for an unknown robot", () => {
    for (const [robot, robotChanges] of changes) {
      expect(activityAt(robotChanges, -1), robot).toBe("free");
      expect(orderAt(robotChanges, -1), robot).toBeNull();
      expect(activityAt(robotChanges, trace.end_tick), robot).toBe("free"); // every order delivered
      expect(orderAt(robotChanges, trace.end_tick), robot).toBeNull();
    }
    expect(activityAt(undefined, 10)).toBe("free");
  });
});
