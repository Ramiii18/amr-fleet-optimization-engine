/**
 * What a robot's events and recorded levels say about it at any moment: its statuses and its
 * battery level.
 *
 * These functions repeat `src/amr_optimizer/replay/state.py`, the reference, and are tested
 * against statuses and levels that the reference computed on real runs (the version 2 golden
 * fixtures). They read a trace; they compute nothing the engine did not.
 *
 * A status is an interval between two events, or a traversal that an event belongs to.
 * Intervals include their first tick and not their last. One still open at the end of the run
 * lasts to the end.
 *
 * A battery level always comes with where it is from:
 * - `reading`: recorded by the engine, on arrival at a node or in an event. Between two
 *   readings the last one is shown unchanged, with the tick it dates from. The engine charges
 *   an edge's energy on arrival, so there is no value in between and none is invented.
 * - `charging_formula`: while the robot stands on a charger, the engine's own formula,
 *   min(capacity, level at the start + ticks * charge per tick). A calculation by the model's
 *   rule, not a recorded reading.
 */

import { type Track, countAtOrBefore, countBefore } from "./motion";
import type { BatteryModel, RobotEvent, RobotEventKind } from "./trace";

/** In the order the Python reference lists them. */
export const STATUSES = [
  "charging",
  "out_of_service",
  "waiting_for_charger",
  "breakdown",
  "delay",
  "corridor_wait",
] as const;
export type Status = (typeof STATUSES)[number];

export const STATUS_LABEL: Record<Status, string> = {
  charging: "Charging",
  out_of_service: "Out of service",
  waiting_for_charger: "Waiting for a charger",
  breakdown: "Breakdown on this edge",
  delay: "Delayed on this edge",
  corridor_wait: "Waiting at the corridor end",
};

/** What each status means, in the words of the model. Shown where the status is shown. */
export const STATUS_NOTE: Record<Status, string> = {
  charging: "Standing on a charger and gaining energy.",
  out_of_service: "Below the low battery threshold: it gets no new job until it has charged.",
  waiting_for_charger: "Out of service, and no charger is free.",
  breakdown:
    "This traversal includes a repair. The engine does not model where on the edge the robot stands still, so its position along the edge is not a recorded place.",
  delay:
    "This traversal takes longer than the edge needs. The engine does not model where on the edge the time is lost.",
  corridor_wait: "At the end of the corridor, waiting for its turn to enter the node.",
};

const STARTS: Partial<Record<RobotEventKind, [end: RobotEventKind, status: Status]>> = {
  charging_started: ["charging_stopped", "charging"],
  out_of_service: ["back_in_service", "out_of_service"],
  charger_wait_started: ["charger_wait_ended", "waiting_for_charger"],
  corridor_wait_started: ["corridor_wait_ended", "corridor_wait"],
};
const TRAVERSALS: Partial<Record<RobotEventKind, Status>> = {
  breakdown: "breakdown",
  delay: "delay",
};

export interface StatusInterval {
  status: Status;
  start: number;
  /** Exclusive. `Infinity` if still open when the run ends. */
  end: number;
  /** The event that opened it. */
  event: RobotEvent;
}

/** Every status interval of one robot, from its own events, in the order they begin. */
export function statusIntervals(track: Track, events: readonly RobotEvent[]): StatusInterval[] {
  const intervals: StatusInterval[] = [];
  const open = new Map<RobotEventKind, StatusInterval>();
  for (const event of events) {
    const started = STARTS[event.kind];
    const traversal = TRAVERSALS[event.kind];
    if (started !== undefined) {
      const interval = { status: started[1], start: event.tick, end: Infinity, event };
      intervals.push(interval);
      open.set(started[0], interval);
    } else if (traversal !== undefined) {
      // From the departure it was drawn at, until the robot enters the next node.
      const index = countBefore(track.depart, event.tick);
      const end = index + 1 < track.arrive.length ? (track.arrive[index + 1] as number) : Infinity;
      intervals.push({ status: traversal, start: event.tick, end, event });
    } else {
      const interval = open.get(event.kind);
      if (interval !== undefined) {
        interval.end = event.tick;
        open.delete(event.kind);
      }
    }
  }
  return intervals;
}

/** The statuses of a robot at any tick, by binary search over the ticks where they change. */
export interface StatusTimeline {
  intervals: StatusInterval[];
  ticks: number[];
  /** For each of `ticks`: the statuses from that tick on, as a bit per entry of `STATUSES`. */
  masks: number[];
}

export function buildStatusTimeline(track: Track, events: readonly RobotEvent[]): StatusTimeline {
  const intervals = statusIntervals(track, events);
  const bounds = new Set<number>();
  for (const interval of intervals) {
    bounds.add(interval.start);
    if (interval.end !== Infinity) bounds.add(interval.end);
  }
  const ticks = [...bounds].sort((a, b) => a - b);
  const masks = ticks.map((tick) => {
    let mask = 0;
    for (const interval of intervals) {
      if (interval.start <= tick && tick < interval.end) {
        mask |= 1 << STATUSES.indexOf(interval.status);
      }
    }
    return mask;
  });
  return { intervals, ticks, masks };
}

export function statusMaskAt(timeline: StatusTimeline | undefined, tick: number): number {
  if (timeline === undefined) return 0;
  const index = countAtOrBefore(timeline.ticks, tick) - 1;
  return index < 0 ? 0 : (timeline.masks[index] as number);
}

export function hasStatus(mask: number, status: Status): boolean {
  return (mask & (1 << STATUSES.indexOf(status))) !== 0;
}

export function statusesAt(timeline: StatusTimeline | undefined, tick: number): Status[] {
  const mask = statusMaskAt(timeline, tick);
  return STATUSES.filter((status) => hasStatus(mask, status));
}

export type LevelSource = "reading" | "charging_formula";

export interface Level {
  /** Energy in the engine's integer units. */
  units: number;
  /** The tick the value dates from: never later than the moment it is shown at. */
  asOf: number;
  source: LevelSource;
}

/**
 * The robot's battery level at `tick`, and where the value comes from.
 *
 * `levels` are the recorded levels on arrival at each visit of `track`, and `events` the
 * robot's own events in time order.
 */
export function levelAt(
  track: Track,
  levels: readonly number[],
  events: readonly RobotEvent[],
  model: BatteryModel,
  tick: number,
): Level {
  const index = Math.max(0, countAtOrBefore(track.arrive, tick) - 1);
  const arrived = track.arrive[index] as number;
  let level: Level = { units: levels[index] as number, asOf: arrived, source: "reading" };
  const whole = Math.floor(tick);
  let charging: RobotEvent | null = null;
  // Events before this visit are already in the level of its arrival.
  for (let i = countBefore(eventTicks(events), arrived); i < events.length; i++) {
    const event = events[i] as RobotEvent;
    if (event.tick > tick) break;
    if (event.kind === "charging_started") charging = event;
    else if (event.kind === "charging_stopped") charging = null;
    // The engine computed this level at this tick.
    if (event.level !== null) level = { units: event.level, asOf: event.tick, source: "reading" };
  }
  if (charging !== null && charging.level !== null && whole > charging.tick && whole > level.asOf) {
    // Later than the last recorded value: the engine's charging formula says more.
    const units = Math.min(
      model.capacity,
      charging.level + (whole - charging.tick) * model.charge_per_tick,
    );
    return { units, asOf: whole, source: "charging_formula" };
  }
  return level;
}

const TICKS = new WeakMap<readonly RobotEvent[], number[]>();

function eventTicks(events: readonly RobotEvent[]): number[] {
  let ticks = TICKS.get(events);
  if (ticks === undefined) {
    ticks = events.map((event) => event.tick);
    TICKS.set(events, ticks);
  }
  return ticks;
}

/** `events` grouped by robot, each group in time order. */
export function eventsByRobot(events: readonly RobotEvent[]): Map<string, RobotEvent[]> {
  const grouped = new Map<string, RobotEvent[]>();
  for (const event of events) {
    const own = grouped.get(event.robot);
    if (own === undefined) grouped.set(event.robot, [event]);
    else own.push(event);
  }
  return grouped;
}

/** A level as a share of the capacity, for display. */
export function levelShare(level: Level, model: BatteryModel): number {
  return level.units / model.capacity;
}
