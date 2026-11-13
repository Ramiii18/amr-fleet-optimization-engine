/**
 * Where a robot is, and what it is doing, at any moment of a recorded run.
 *
 * These functions repeat `src/amr_optimizer/viz/motion.py`, the reference, and are tested
 * against positions and activities that the reference computed on real runs (the golden
 * fixtures in `tests/data/replay/`). They read a trace; they never plan or simulate.
 *
 * Position: a robot stands at a visit's node from its arrival to its departure, both included.
 * Between a departure and the next arrival it drives one edge at constant speed, so its
 * position is linear in time. Before its first visit it is at its first node, and after its
 * last departure it stays at its last node.
 *
 * Activity: the robot's last assignment, load or delivery event at or before the tick decides.
 * An event at tick t already counts at tick t.
 */

import type { RobotEvent, Trace, TraceNode, TraceRobot } from "./trace";

/** The three activities the event log supports. Nothing else is recorded, so nothing else is shown. */
export type Activity = "free" | "to pickup" | "loaded";

/** One robot's recorded movement, laid out for fast lookups. */
export interface Track {
  id: string;
  /** Node id of each visit. */
  nodes: string[];
  arrive: Float64Array;
  /** Departure tick of each visit; `Infinity` for a hold. */
  depart: Float64Array;
  /** Position of each visit's node, in metres. */
  xs: Float64Array;
  ys: Float64Array;
  /**
   * For the drive that leaves each visit: the tick the robot reaches the end of the corridor.
   * It is the arrival at the next visit, unless the trace records that the robot had to wait at
   * the end of the corridor before entering the node (a `corridor_wait_started` event): then it
   * is the tick that wait began. `Infinity` for the last visit.
   */
  reach: Float64Array;
}

/**
 * `events` are the robot's own events, if the trace has any (version 2). Without them, or
 * without a corridor wait among them, positions are exactly those of `viz/motion.py`.
 */
export function buildTrack(
  robot: TraceRobot,
  nodes: ReadonlyMap<string, TraceNode>,
  events: readonly RobotEvent[] = [],
): Track {
  const count = robot.visits.length;
  const track: Track = {
    id: robot.id,
    nodes: new Array<string>(count),
    arrive: new Float64Array(count),
    depart: new Float64Array(count),
    xs: new Float64Array(count),
    ys: new Float64Array(count),
    reach: new Float64Array(count).fill(Infinity),
  };
  robot.visits.forEach(([nodeId, arrive, depart], i) => {
    const node = nodes.get(nodeId);
    if (node === undefined) throw new Error(`robot ${robot.id}: unknown node ${nodeId}`);
    track.nodes[i] = nodeId;
    track.arrive[i] = arrive;
    track.depart[i] = depart ?? Infinity;
    track.xs[i] = node.x;
    track.ys[i] = node.y;
    if (i > 0) track.reach[i - 1] = arrive;
  });
  for (const event of events) {
    if (event.kind !== "corridor_wait_started") continue;
    // The drive it belongs to: the last departure before the wait began.
    const index = countBefore(track.depart, event.tick) - 1;
    if (index >= 0 && index < count - 1) track.reach[index] = event.tick;
  }
  return track;
}

/** How many of the ascending `values` are < `x` (Python's `bisect_left`). */
export function countBefore(values: ArrayLike<number>, x: number): number {
  let low = 0;
  let high = values.length;
  while (low < high) {
    const middle = (low + high) >>> 1;
    if ((values[middle] as number) < x) low = middle + 1;
    else high = middle;
  }
  return low;
}

/** How many of the ascending `values` are <= `x` (Python's `bisect_right`). */
export function countAtOrBefore(values: ArrayLike<number>, x: number): number {
  let low = 0;
  let high = values.length;
  while (low < high) {
    const middle = (low + high) >>> 1;
    if ((values[middle] as number) <= x) low = middle + 1;
    else high = middle;
  }
  return low;
}

export interface Pose {
  x: number;
  y: number;
  /** Index of the visit the robot is in, or has last left. */
  visit: number;
  /** True while it drives from visit `visit` to visit `visit + 1`. */
  moving: boolean;
  /** Share of that edge already driven, in time (0 while standing). */
  share: number;
  /** True while it stands at the end of the corridor, not yet allowed into the next node. */
  waiting: boolean;
}

/**
 * The robot's pose at `tick`, which may fall between whole ticks.
 *
 * The visit is found by binary search over arrivals: O(log n), against the reference's scan.
 * Arrivals increase strictly, so the last visit with `arrive <= tick` is the one the scan
 * stops at, and both give the same position.
 */
export function poseAt(track: Track, tick: number): Pose {
  const last = track.arrive.length - 1;
  const index = Math.max(0, countAtOrBefore(track.arrive, tick) - 1);
  const x = track.xs[index] as number;
  const y = track.ys[index] as number;
  if (index === last || tick <= (track.depart[index] as number)) {
    return { x, y, visit: index, moving: false, share: 0, waiting: false };
  }
  const depart = track.depart[index] as number;
  const reach = track.reach[index] as number;
  if (tick >= reach) {
    // At the end of the corridor, by the node it will enter (`replay.state.position_at`).
    return {
      x: track.xs[index + 1] as number,
      y: track.ys[index + 1] as number,
      visit: index,
      moving: true,
      share: 1,
      waiting: true,
    };
  }
  const share = (tick - depart) / (reach - depart);
  return {
    x: x + share * ((track.xs[index + 1] as number) - x),
    y: y + share * ((track.ys[index + 1] as number) - y),
    visit: index,
    moving: true,
    share,
    waiting: false,
  };
}

/** `poseAt` as a plain position, the signature of the Python reference. */
export function positionAt(track: Track, tick: number): [x: number, y: number] {
  const pose = poseAt(track, tick);
  return [pose.x, pose.y];
}

/** A playback position kept inside the run: 0 to `endTick`. A trace of a run that was cut
 * short can hold visits after `endTick`; the viewer never plays into them. */
export function clampTick(tick: number, endTick: number): number {
  if (!(tick > 0)) return 0; // also NaN
  return tick > endTick ? endTick : tick;
}

/** Every change of one robot's activity, in time order, with the order that caused it. */
export interface ActivityChanges {
  ticks: number[];
  activities: Activity[];
  orders: string[];
}

const CHANGES: Partial<Record<string, Activity>> = {
  assigned: "to pickup",
  loaded: "loaded",
  delivered: "free",
};

export function buildActivityChanges(trace: Trace): Map<string, ActivityChanges> {
  const changes = new Map<string, ActivityChanges>();
  for (const robot of trace.robots) {
    changes.set(robot.id, { ticks: [], activities: [], orders: [] });
  }
  for (const event of trace.events) {
    const activity = CHANGES[event.kind];
    if (event.robot === null || activity === undefined) continue;
    const robot = changes.get(event.robot);
    if (robot === undefined) continue;
    robot.ticks.push(event.tick);
    robot.activities.push(activity);
    robot.orders.push(event.order);
  }
  return changes;
}

export function activityAt(changes: ActivityChanges | undefined, tick: number): Activity {
  if (changes === undefined) return "free";
  const index = countAtOrBefore(changes.ticks, tick) - 1;
  return index < 0 ? "free" : (changes.activities[index] as Activity);
}

/** The order the robot is working on at `tick`, or null while it is free. */
export function orderAt(changes: ActivityChanges | undefined, tick: number): string | null {
  if (changes === undefined) return null;
  const index = countAtOrBefore(changes.ticks, tick) - 1;
  if (index < 0 || changes.activities[index] === "free") return null;
  return changes.orders[index] as string;
}
