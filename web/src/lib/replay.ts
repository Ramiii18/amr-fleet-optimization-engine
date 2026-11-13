/**
 * A trace prepared for playback: lookups built once, so that drawing a frame is cheap.
 *
 * Everything here is read from the trace. The only derived values are sums and lookups over
 * recorded data (metres along a recorded path, an order's state from its logged steps).
 */

import {
  type ActivityChanges,
  type Track,
  buildActivityChanges,
  buildTrack,
  countAtOrBefore,
} from "./motion";
import {
  type Level,
  STATUSES,
  type Status,
  type StatusTimeline,
  buildStatusTimeline,
  eventsByRobot,
  hasStatus,
  levelAt,
  statusMaskAt,
} from "./state";
import type { BatteryModel, RobotEvent, Trace, TraceConflict, TraceNode } from "./trace";

/** One order and the tick of each logged step of its life (null if the step never happened). */
export interface OrderLife {
  id: string;
  pickup: string;
  dropoff: string;
  /** The tick the order was planned to be released at (from the scenario). */
  releaseTick: number;
  released: number | null;
  assigned: number | null;
  pickupReached: number | null;
  loaded: number | null;
  dropoffReached: number | null;
  delivered: number | null;
  robot: string | null;
  /** Metres of the routes planned at assignment (empty) and at loading (loaded). */
  emptyM: number;
  loadedM: number;
}

/** Where an order is in its life. Each state begins at one logged event. */
export type OrderState =
  | "unreleased"
  | "pending"
  | "to_pickup"
  | "loading"
  | "carrying"
  | "unloading"
  | "delivered";

export const ORDER_STATES: readonly OrderState[] = [
  "unreleased",
  "pending",
  "to_pickup",
  "loading",
  "carrying",
  "unloading",
  "delivered",
];

export const ORDER_STATE_LABEL: Record<OrderState, string> = {
  unreleased: "Not released",
  pending: "Waiting for a robot",
  to_pickup: "Robot driving to pickup",
  loading: "Loading",
  carrying: "Carried to drop-off",
  unloading: "Unloading",
  delivered: "Delivered",
};

export function orderStateAt(order: OrderLife, tick: number): OrderState {
  const reached = (step: number | null): boolean => step !== null && step <= tick;
  if (reached(order.delivered)) return "delivered";
  if (reached(order.dropoffReached)) return "unloading";
  if (reached(order.loaded)) return "carrying";
  if (reached(order.pickupReached)) return "loading";
  if (reached(order.assigned)) return "to_pickup";
  if (reached(order.released)) return "pending";
  return "unreleased";
}

/** A connection between two nodes, for drawing: one line for a two-way corridor. */
export interface Corridor {
  a: TraceNode;
  b: TraceNode;
  /** True if robots can drive only from `a` to `b`. */
  oneWay: boolean;
  lengthM: number;
}

export interface Bounds {
  minX: number;
  minY: number;
  maxX: number;
  maxY: number;
}

export interface Replay {
  trace: Trace;
  tickS: number;
  endTick: number;
  nodes: Map<string, TraceNode>;
  corridors: Corridor[];
  bounds: Bounds;
  tracks: Track[];
  trackById: Map<string, Track>;
  /** Per robot: metres driven on arrival at each visit, summed over the recorded edges. */
  odometer: Map<string, Float64Array>;
  activity: Map<string, ActivityChanges>;
  orders: OrderLife[];
  orderById: Map<string, OrderLife>;
  /** Ticks at which at least one event was logged (order or robot events), ascending and
   * without repeats. */
  eventTicks: number[];
  conflicts: TraceConflict[];
  /**
   * False for a version 1 trace: it records no robot events and no battery levels, so charging,
   * service state, delays, breakdowns and waits are unknown for that run, not absent from it.
   */
  observed: boolean;
  /** Each robot's own events, in time order (empty lists without `observed`). */
  robotEvents: Map<string, RobotEvent[]>;
  statuses: Map<string, StatusTimeline>;
  /** The engine's battery model, or null for a run without one (or a version 1 trace). */
  battery: BatteryModel | null;
  /** Per robot: the recorded level on arrival at each visit, when there is a battery model. */
  levels: Map<string, number[]>;
}

export function boundsOf(nodes: Iterable<TraceNode>): Bounds {
  const bounds = { minX: Infinity, minY: Infinity, maxX: -Infinity, maxY: -Infinity };
  for (const node of nodes) {
    bounds.minX = Math.min(bounds.minX, node.x);
    bounds.minY = Math.min(bounds.minY, node.y);
    bounds.maxX = Math.max(bounds.maxX, node.x);
    bounds.maxY = Math.max(bounds.maxY, node.y);
  }
  if (bounds.minX === Infinity) return { minX: 0, minY: 0, maxX: 1, maxY: 1 };
  return bounds;
}

/** Directed edges as drawable corridors: a pair of opposite edges becomes one two-way line. */
export function corridorsOf(
  nodes: ReadonlyMap<string, TraceNode>,
  edges: readonly { source: string; target: string; length_m: number }[],
): Corridor[] {
  const keys = new Set(edges.map((edge) => `${edge.source}\u0000${edge.target}`));
  const corridors: Corridor[] = [];
  for (const edge of edges) {
    const twoWay = keys.has(`${edge.target}\u0000${edge.source}`);
    if (twoWay && edge.source > edge.target) continue; // drawn once, from the smaller id
    const a = nodes.get(edge.source);
    const b = nodes.get(edge.target);
    if (a === undefined || b === undefined) continue;
    corridors.push({ a, b, oneWay: !twoWay, lengthM: edge.length_m });
  }
  return corridors;
}

export function buildReplay(trace: Trace): Replay {
  const nodes = new Map(trace.map.nodes.map((node) => [node.id, node]));
  const lengths = new Map(
    trace.map.edges.map((edge) => [`${edge.source}\u0000${edge.target}`, edge.length_m]),
  );
  const robotEvents = eventsByRobot(trace.robot_events);
  for (const robot of trace.robots) {
    if (!robotEvents.has(robot.id)) robotEvents.set(robot.id, []);
  }
  const tracks = trace.robots.map((robot) => buildTrack(robot, nodes, robotEvents.get(robot.id)));
  const statuses = new Map(
    tracks.map((track) => [track.id, buildStatusTimeline(track, robotEvents.get(track.id) ?? [])]),
  );
  const levels = new Map<string, number[]>();
  for (const robot of trace.robots) {
    if (robot.levels !== null) levels.set(robot.id, robot.levels);
  }
  const odometer = new Map<string, Float64Array>();
  for (const track of tracks) {
    const metres = new Float64Array(track.nodes.length);
    for (let i = 1; i < track.nodes.length; i++) {
      const length = lengths.get(`${track.nodes[i - 1]}\u0000${track.nodes[i]}`) ?? 0;
      metres[i] = (metres[i - 1] as number) + length;
    }
    odometer.set(track.id, metres);
  }

  const orders: OrderLife[] = trace.orders.map((order) => ({
    id: order.id,
    pickup: order.pickup,
    dropoff: order.dropoff,
    releaseTick: order.release_tick,
    released: null,
    assigned: null,
    pickupReached: null,
    loaded: null,
    dropoffReached: null,
    delivered: null,
    robot: null,
    emptyM: 0,
    loadedM: 0,
  }));
  const orderById = new Map(orders.map((order) => [order.id, order]));
  const eventTicks: number[] = [];
  for (const event of trace.events) {
    if (eventTicks[eventTicks.length - 1] !== event.tick) eventTicks.push(event.tick);
    const order = orderById.get(event.order);
    if (order === undefined) continue;
    if (event.robot !== null) order.robot = event.robot;
    // The first event of each kind counts, as in the KPI definitions.
    switch (event.kind) {
      case "released":
        order.released ??= event.tick;
        break;
      case "assigned":
        if (order.assigned === null) order.emptyM = event.distance_m;
        order.assigned ??= event.tick;
        break;
      case "pickup_reached":
        order.pickupReached ??= event.tick;
        break;
      case "loaded":
        if (order.loaded === null) order.loadedM = event.distance_m;
        order.loaded ??= event.tick;
        break;
      case "dropoff_reached":
        order.dropoffReached ??= event.tick;
        break;
      case "delivered":
        order.delivered ??= event.tick;
        break;
    }
  }

  return {
    trace,
    tickS: trace.tick_s,
    endTick: trace.end_tick,
    nodes,
    corridors: corridorsOf(nodes, trace.map.edges),
    bounds: boundsOf(nodes.values()),
    tracks,
    trackById: new Map(tracks.map((track) => [track.id, track])),
    odometer,
    activity: buildActivityChanges(trace),
    orders,
    orderById,
    eventTicks: [...new Set([...eventTicks, ...trace.robot_events.map((event) => event.tick)])].sort(
      (a, b) => a - b,
    ),
    conflicts: trace.conflicts.listed,
    observed: trace.schema_version >= 2,
    robotEvents,
    statuses,
    battery: trace.battery_model,
    levels,
  };
}

/** How many robots have each status at `tick`. */
export function statusCounts(replay: Replay, tick: number): Record<Status, number> {
  const counts = Object.fromEntries(STATUSES.map((status) => [status, 0])) as Record<Status, number>;
  for (const track of replay.tracks) {
    const mask = statusMaskAt(replay.statuses.get(track.id), tick);
    for (const status of STATUSES) if (hasStatus(mask, status)) counts[status] += 1;
  }
  return counts;
}

/** A robot's battery level at `tick`, or null if the run has no battery model. */
export function robotLevelAt(replay: Replay, robotId: string, tick: number): Level | null {
  const track = replay.trackById.get(robotId);
  const levels = replay.levels.get(robotId);
  if (replay.battery === null || track === undefined || levels === undefined) return null;
  return levelAt(track, levels, replay.robotEvents.get(robotId) ?? [], replay.battery, tick);
}

/** How many orders are in each state at `tick`. */
export function orderCounts(orders: readonly OrderLife[], tick: number): Record<OrderState, number> {
  const counts: Record<OrderState, number> = {
    unreleased: 0,
    pending: 0,
    to_pickup: 0,
    loading: 0,
    carrying: 0,
    unloading: 0,
    delivered: 0,
  };
  for (const order of orders) counts[orderStateAt(order, tick)] += 1;
  return counts;
}

/** The listed conflicts that are happening at `tick` (their ticks include both ends). */
export function conflictsAt(conflicts: readonly TraceConflict[], tick: number): TraceConflict[] {
  return conflicts.filter((conflict) => conflict.start <= tick && tick <= conflict.end);
}

/** The nearest event tick strictly after (`direction` 1) or before (-1) `tick`, or null. */
export function neighbourTick(ticks: readonly number[], tick: number, direction: 1 | -1): number | null {
  if (direction === 1) {
    const index = countAtOrBefore(ticks, tick);
    return ticks[index] ?? null;
  }
  const index = countAtOrBefore(ticks, tick - 1e-9) - 1;
  return ticks[index] ?? null;
}

/** Metres a robot has driven by `tick`, along its recorded path. */
export function drivenMetres(replay: Replay, robotId: string, visit: number, share: number): number {
  const metres = replay.odometer.get(robotId);
  if (metres === undefined) return 0;
  const here = metres[visit] ?? 0;
  const next = metres[visit + 1] ?? here;
  return here + share * (next - here);
}
