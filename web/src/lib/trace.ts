/**
 * The replay trace format (schema versions 1 and 2), as the engine writes it.
 *
 * The format is defined in Python: `src/amr_optimizer/replay/trace.py` (D33, D35). This file reads
 * it and checks it. Nothing here computes anything about a run: a trace is the whole truth,
 * and what it does not contain is not shown.
 *
 * Every time is an integer tick of `tick_s` seconds. A visit is `[node, arrive, depart]`; a
 * null departure is a hold (the robot stays with no planned departure).
 */

export const TRACE_FORMAT = "amr-replay-trace";
/** The versions this viewer reads. Version 2 adds robot events and battery levels (D35); a
 * version 1 trace has neither, and the viewer then says so instead of showing anything. */
export const READABLE_SCHEMA_VERSIONS = [1, 2] as const;
export type SchemaVersion = (typeof READABLE_SCHEMA_VERSIONS)[number];

export const ROBOT_EVENT_KINDS = [
  "charging_started",
  "charging_stopped",
  "out_of_service",
  "back_in_service",
  "charger_wait_started",
  "charger_wait_ended",
  "charger_trip_started",
  "delay",
  "breakdown",
  "corridor_wait_started",
  "corridor_wait_ended",
  "replanned",
  "moved_out_of_the_way",
] as const;
export type RobotEventKind = (typeof ROBOT_EVENT_KINDS)[number];

/** Which of `target`, `ticks`, `level` and `reason` each kind of robot event has: the table of
 * `ROBOT_EVENT_FIELDS` in the Python package. */
const EVENT_FIELDS: Record<RobotEventKind, readonly string[]> = {
  charging_started: ["level"],
  charging_stopped: ["level"],
  out_of_service: ["level"],
  back_in_service: ["level"],
  charger_wait_started: [],
  charger_wait_ended: [],
  charger_trip_started: ["target", "level", "reason"],
  delay: ["target", "ticks"],
  breakdown: ["target", "ticks"],
  corridor_wait_started: ["target"],
  corridor_wait_ended: ["target"],
  replanned: [],
  moved_out_of_the_way: ["target"],
};

/** One thing that happened to a robot besides the steps of an order. `node` is where the robot
 * is: the node it stands on, or the node it left for a corridor. */
export interface RobotEvent {
  tick: number;
  kind: RobotEventKind;
  robot: string;
  node: string;
  target: string | null;
  ticks: number | null;
  /** The robot's energy at that tick, in the engine's integer units. */
  level: number | null;
  reason: string | null;
}

/** The numbers of the engine's battery model, in its integer energy units. A robot on a charger
 * for t ticks from level e has min(capacity, e + t * charge_per_tick). */
export interface BatteryModel {
  capacity: number;
  charge_per_tick: number;
  low: number;
  high: number;
  back_in_service: number;
  units_per_metre: number;
}

export const NODE_KINDS = ["intersection", "station", "storage", "charger", "parking"] as const;
export type NodeKind = (typeof NODE_KINDS)[number];

export const EVENT_KINDS = [
  "released",
  "assigned",
  "pickup_reached",
  "loaded",
  "dropoff_reached",
  "delivered",
] as const;
export type EventKind = (typeof EVENT_KINDS)[number];

export const CONFLICT_KINDS = ["vertex", "head-on", "following"] as const;
export type ConflictKind = (typeof CONFLICT_KINDS)[number];

export interface TraceNode {
  id: string;
  x: number;
  y: number;
  kind: NodeKind;
}

export interface TraceEdge {
  source: string;
  target: string;
  length_m: number;
}

export type TraceVisit = readonly [node: string, arrive: number, depart: number | null];

export interface TraceRobot {
  id: string;
  visits: TraceVisit[];
  /** The energy on arrival at each visit, in units; null without a battery model, and in
   * version 1 traces. */
  levels: number[] | null;
}

export interface TraceOrder {
  id: string;
  pickup: string;
  dropoff: string;
  release_tick: number;
}

export interface TraceEvent {
  tick: number;
  kind: EventKind;
  order: string;
  node: string;
  robot: string | null;
  distance_m: number;
}

export interface TraceConflict {
  kind: ConflictKind;
  /** One node, or the two ends of a corridor. */
  resource: string[];
  robots: [string, string];
  start: number;
  end: number;
}

export type Scalar = number | string | null;

export interface Trace {
  format: typeof TRACE_FORMAT;
  schema_version: SchemaVersion;
  source: string;
  run: Record<string, unknown>;
  policy: string;
  traffic: boolean;
  battery: boolean;
  tick_s: number;
  end_tick: number;
  speed_mps: number;
  load_ticks: number;
  unload_ticks: number;
  map: { nodes: TraceNode[]; edges: TraceEdge[] };
  robots: TraceRobot[];
  orders: TraceOrder[];
  events: TraceEvent[];
  outcome: {
    delivered: string[];
    in_progress: string[];
    pending: string[];
    unreleased: string[];
  };
  counters: {
    breakdowns: number;
    breakdown_ticks: number;
    replans: number;
    moves_out_of_the_way: number;
    deferred_assignments: number;
  };
  kpis: Record<string, Scalar>;
  charging: Record<string, Scalar> | null;
  conflicts: { total: number; listed: TraceConflict[] };
  /** Null without a battery model, and in version 1 traces. */
  battery_model: BatteryModel | null;
  /** In time order. Empty in version 1 traces, which do not record them. */
  robot_events: RobotEvent[];
}

/** A trace that is malformed, or of a version this viewer does not read. */
export class TraceFormatError extends Error {
  override name = "TraceFormatError";
}

function fail(where: string, message: string): never {
  throw new TraceFormatError(`${where}: ${message}`);
}

type Json = Record<string, unknown>;

function object(value: unknown, where: string, fields?: readonly string[]): Json {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    fail(where, "expected an object");
  }
  const obj = value as Json;
  if (fields !== undefined) {
    const missing = fields.filter((field) => !(field in obj));
    if (missing.length > 0) fail(where, `missing field(s) ${missing.join(", ")}`);
    const unknown = Object.keys(obj).filter((key) => !fields.includes(key));
    if (unknown.length > 0) fail(where, `unknown field(s) ${unknown.join(", ")}`);
  }
  return obj;
}

function list(value: unknown, where: string): unknown[] {
  if (!Array.isArray(value)) fail(where, "expected a list");
  return value;
}

function text(value: unknown, where: string): string {
  if (typeof value !== "string") fail(where, "expected a string");
  return value;
}

function real(value: unknown, where: string): number {
  if (typeof value !== "number" || !Number.isFinite(value)) fail(where, "expected a number");
  return value;
}

function whole(value: unknown, where: string): number {
  if (typeof value !== "number" || !Number.isSafeInteger(value) || value < 0) {
    fail(where, "expected a non-negative integer");
  }
  return value;
}

function flag(value: unknown, where: string): boolean {
  if (typeof value !== "boolean") fail(where, "expected true or false");
  return value;
}

function oneOf<T extends string>(value: unknown, allowed: readonly T[], where: string): T {
  if (typeof value !== "string" || !(allowed as readonly string[]).includes(value)) {
    fail(where, `expected one of ${allowed.join(", ")}`);
  }
  return value as T;
}

function scalars(value: unknown, where: string): Record<string, Scalar> {
  const out: Record<string, Scalar> = {};
  for (const [key, item] of Object.entries(object(value, where))) {
    if (item !== null && typeof item !== "number" && typeof item !== "string") {
      fail(`${where}.${key}`, "expected a number, a string or null");
    }
    out[key] = item;
  }
  return out;
}

const TOP = [
  "format",
  "schema_version",
  "source",
  "run",
  "policy",
  "traffic",
  "battery",
  "tick_s",
  "end_tick",
  "speed_mps",
  "load_ticks",
  "unload_ticks",
  "map",
  "robots",
  "orders",
  "events",
  "outcome",
  "counters",
  "kpis",
  "charging",
  "conflicts",
] as const;

const BATTERY_FIELDS = [
  "capacity",
  "charge_per_tick",
  "low",
  "high",
  "back_in_service",
  "units_per_metre",
] as const;
const ROBOT_EVENT_KEYS = [
  "tick",
  "kind",
  "robot",
  "node",
  "target",
  "ticks",
  "level",
  "reason",
] as const;
const OUTCOME = ["delivered", "in_progress", "pending", "unreleased"] as const;
const COUNTERS = [
  "breakdowns",
  "breakdown_ticks",
  "replans",
  "moves_out_of_the_way",
  "deferred_assignments",
] as const;

/**
 * Every robot event must agree with the robot's recorded path (`_check_event_places` in the
 * Python package). Statuses and corridor waits come from the events and positions from the
 * path: if the two disagreed, the viewer would show a robot charging in an aisle or delayed
 * while it stands.
 */
function checkEventPlaces(
  events: readonly RobotEvent[],
  robots: readonly TraceRobot[],
  nodes: readonly TraceNode[],
  edgeKeys: ReadonlySet<string>,
): void {
  const paths = new Map(robots.map((robot) => [robot.id, robot.visits]));
  const chargers = new Set(nodes.filter((node) => node.kind === "charger").map((node) => node.id));
  events.forEach((event, i) => {
    const where = `robot_events[${i}]`;
    const visits = paths.get(event.robot) ?? [];
    // The visit the robot is in at that tick, or has last left.
    let at = 0;
    for (let low = 0, high = visits.length; low < high; ) {
      const middle = (low + high) >>> 1;
      if ((visits[middle] as TraceVisit)[1] <= event.tick) {
        at = middle;
        low = middle + 1;
      } else high = middle;
    }
    const current = visits[at] as TraceVisit;
    const following = visits[at + 1];
    // An event at the tick of an arrival may have been recorded just before the robot entered
    // the node: then it still names the node the robot came from.
    const before = at > 0 && current[1] === event.tick ? visits[at - 1] : undefined;
    const robot = `robot ${event.robot}`;
    const { kind, node, target, tick } = event;
    if (kind === "corridor_wait_ended") {
      if (before === undefined || before[0] !== node || current[0] !== target) {
        fail(where, `${robot} does not enter ${target} from ${node} at tick ${tick}`);
      }
      return;
    }
    if (node !== current[0] && node !== before?.[0]) {
      fail(where, `${robot} is not at ${node} at tick ${tick}`);
    }
    if (kind === "delay" || kind === "breakdown" || kind === "corridor_wait_started") {
      // These belong to one traversal: the robot has left `node` for `target`.
      const depart = current[2] ?? Infinity;
      const left = kind === "corridor_wait_started" ? depart < tick : depart === tick;
      const towards =
        following !== undefined
          ? following[0] === target
          : edgeKeys.has(`${node}\u0000${target}`);
      if (node !== current[0] || !left || !towards) {
        fail(where, `${robot} is not on its way from ${node} to ${target} at tick ${tick}`);
      }
    } else if (kind === "charging_started" || kind === "charging_stopped") {
      if (!chargers.has(node)) fail(where, `${node} is not a charger`);
      if (kind === "charging_stopped" && current[2] !== tick) {
        fail(where, `${robot} does not leave ${node} at tick ${tick}`);
      }
    } else if (kind === "charger_trip_started") {
      if (target === null || !chargers.has(target)) fail(where, `${target} is not a charger`);
    }
  });
}

/**
 * Check parsed JSON and return it as a `Trace`. Throws `TraceFormatError` with the place of the
 * first problem.
 *
 * Format and version are checked before anything else: a later version may differ anywhere.
 * Beyond structure and types, the checks are those a viewer depends on: every visit, order and
 * event is on the map, every step drives an existing edge, robots start at tick 0, and events
 * are in time order.
 */
export function parseTrace(data: unknown): Trace {
  const head = object(data, "trace");
  if (head["format"] !== TRACE_FORMAT) {
    fail("trace.format", `this is not a replay trace (expected "${TRACE_FORMAT}")`);
  }
  const version = READABLE_SCHEMA_VERSIONS.find((known) => known === head["schema_version"]);
  if (version === undefined) {
    fail(
      "trace.schema_version",
      `version ${JSON.stringify(head["schema_version"])} is not supported; ` +
        `this viewer reads versions ${READABLE_SCHEMA_VERSIONS.join(" and ")}`,
    );
  }
  const v2 = version === 2;
  const top = object(data, "trace", v2 ? [...TOP, "battery_model", "robot_events"] : TOP);

  const map = object(top["map"], "map", ["nodes", "edges"]);
  const nodes = list(map["nodes"], "map.nodes").map((item, i): TraceNode => {
    const where = `map.nodes[${i}]`;
    const node = object(item, where, ["id", "x", "y", "kind"]);
    return {
      id: text(node["id"], `${where}.id`),
      x: real(node["x"], `${where}.x`),
      y: real(node["y"], `${where}.y`),
      kind: oneOf(node["kind"], NODE_KINDS, `${where}.kind`),
    };
  });
  const known = new Set<string>();
  for (const node of nodes) {
    if (known.has(node.id)) fail("map.nodes", `duplicate node id ${node.id}`);
    known.add(node.id);
  }
  const onMap = (id: string, where: string): string =>
    known.has(id) ? id : fail(where, `unknown node ${id}`);

  const edgeKeys = new Set<string>();
  const edges = list(map["edges"], "map.edges").map((item, i): TraceEdge => {
    const where = `map.edges[${i}]`;
    const edge = object(item, where, ["source", "target", "length_m"]);
    const source = onMap(text(edge["source"], `${where}.source`), `${where}.source`);
    const target = onMap(text(edge["target"], `${where}.target`), `${where}.target`);
    const length_m = real(edge["length_m"], `${where}.length_m`);
    if (length_m <= 0) fail(`${where}.length_m`, "must be positive");
    edgeKeys.add(`${source}\u0000${target}`);
    return { source, target, length_m };
  });

  const robotIds = new Set<string>();
  const robots = list(top["robots"], "robots").map((item, i): TraceRobot => {
    const where = `robots[${i}]`;
    const robot = object(item, where, v2 ? ["id", "visits", "levels"] : ["id", "visits"]);
    const id = text(robot["id"], `${where}.id`);
    if (robotIds.has(id)) fail(where, `duplicate robot id ${id}`);
    robotIds.add(id);
    const raw = list(robot["visits"], `${where}.visits`);
    if (raw.length === 0) fail(`${where}.visits`, "a robot needs at least one visit");
    const visits = raw.map((visit, j): TraceVisit => {
      const at = `${where}.visits[${j}]`;
      if (!Array.isArray(visit) || visit.length !== 3) fail(at, "expected [node, arrive, depart]");
      const node = onMap(text(visit[0], `${at}[0]`), `${at}[0]`);
      const arrive = whole(visit[1], `${at}[1]`);
      const depart = visit[2] === null ? null : whole(visit[2], `${at}[2]`);
      if (depart !== null && depart < arrive) fail(at, "departure before arrival");
      return [node, arrive, depart];
    });
    const first = visits[0];
    if (first === undefined || first[1] !== 0) {
      fail(`${where}.visits[0]`, "the first visit must start at tick 0");
    }
    for (let j = 1; j < visits.length; j++) {
      const [before, after] = [visits[j - 1], visits[j]];
      if (before === undefined || after === undefined) continue;
      const at = `${where}.visits[${j}]`;
      if (before[2] === null) fail(at, "only the last visit can be a hold");
      if (after[1] <= before[2]) fail(at, "arrival must come after the previous departure");
      if (!edgeKeys.has(`${before[0]}\u0000${after[0]}`)) {
        fail(at, `no edge ${before[0]} -> ${after[0]}`);
      }
    }
    let levels: number[] | null = null;
    if (v2 && robot["levels"] !== null) {
      levels = list(robot["levels"], `${where}.levels`).map((level, j) =>
        whole(level, `${where}.levels[${j}]`),
      );
      if (levels.length !== visits.length) fail(`${where}.levels`, "needs one level per visit");
    }
    return { id, visits, levels };
  });
  if (robots.length === 0) fail("robots", "a trace needs at least one robot");

  let battery_model: BatteryModel | null = null;
  if (v2 && top["battery_model"] !== null) {
    const model = object(top["battery_model"], "battery_model", BATTERY_FIELDS);
    const positive = (name: (typeof BATTERY_FIELDS)[number]): number => {
      const value = whole(model[name], `battery_model.${name}`);
      return value > 0 ? value : fail(`battery_model.${name}`, "must be positive");
    };
    battery_model = {
      capacity: positive("capacity"),
      charge_per_tick: positive("charge_per_tick"),
      low: positive("low"),
      high: positive("high"),
      back_in_service: positive("back_in_service"),
      units_per_metre: positive("units_per_metre"),
    };
    const { low, high, capacity, back_in_service } = battery_model;
    if (!(low < high && high <= capacity)) fail("battery_model", "needs low < high <= capacity");
    if (back_in_service !== low && back_in_service !== high) {
      fail("battery_model.back_in_service", "must be the low or the high threshold");
    }
    if (top["battery"] !== true) fail("battery_model", "given for a run without a battery");
  }
  for (const robot of robots) {
    if ((robot.levels === null) !== (battery_model === null)) {
      fail(`robot ${robot.id}`, "levels are given exactly with a battery model");
    }
    const capacity = battery_model?.capacity ?? 0;
    if (robot.levels?.some((level) => level > capacity)) {
      fail(`robot ${robot.id}`, `a level is outside 0 to ${capacity}`);
    }
  }

  const orderIds = new Set<string>();
  const orders = list(top["orders"], "orders").map((item, i): TraceOrder => {
    const where = `orders[${i}]`;
    const order = object(item, where, ["id", "pickup", "dropoff", "release_tick"]);
    const id = text(order["id"], `${where}.id`);
    if (orderIds.has(id)) fail(where, `duplicate order id ${id}`);
    orderIds.add(id);
    return {
      id,
      pickup: onMap(text(order["pickup"], `${where}.pickup`), `${where}.pickup`),
      dropoff: onMap(text(order["dropoff"], `${where}.dropoff`), `${where}.dropoff`),
      release_tick: whole(order["release_tick"], `${where}.release_tick`),
    };
  });

  const end_tick = whole(top["end_tick"], "trace.end_tick");
  let previous = 0;
  const events = list(top["events"], "events").map((item, i): TraceEvent => {
    const where = `events[${i}]`;
    const event = object(item, where, ["tick", "kind", "order", "node", "robot", "distance_m"]);
    const tick = whole(event["tick"], `${where}.tick`);
    if (tick < previous || tick > end_tick) {
      fail(`${where}.tick`, "out of time order or outside the run");
    }
    previous = tick;
    const kind = oneOf(event["kind"], EVENT_KINDS, `${where}.kind`);
    const order = text(event["order"], `${where}.order`);
    if (!orderIds.has(order)) fail(`${where}.order`, `unknown order ${order}`);
    const robot = event["robot"] === null ? null : text(event["robot"], `${where}.robot`);
    if ((robot === null) !== (kind === "released")) {
      fail(where, "every event except a release names a robot");
    }
    if (robot !== null && !robotIds.has(robot)) fail(`${where}.robot`, `unknown robot ${robot}`);
    return {
      tick,
      kind,
      order,
      node: onMap(text(event["node"], `${where}.node`), `${where}.node`),
      robot,
      distance_m: real(event["distance_m"], `${where}.distance_m`),
    };
  });

  const outcomeIn = object(top["outcome"], "outcome", OUTCOME);
  const ids = (key: (typeof OUTCOME)[number]): string[] =>
    list(outcomeIn[key], `outcome.${key}`).map((id, i) => text(id, `outcome.${key}[${i}]`));
  const countersIn = object(top["counters"], "counters", COUNTERS);
  const count = (key: (typeof COUNTERS)[number]): number =>
    whole(countersIn[key], `counters.${key}`);

  const conflictsIn = object(top["conflicts"], "conflicts", ["total", "listed"]);
  const listed = list(conflictsIn["listed"], "conflicts.listed").map((item, i): TraceConflict => {
    const where = `conflicts.listed[${i}]`;
    const conflict = object(item, where, ["kind", "resource", "robots", "start", "end"]);
    const kind = oneOf(conflict["kind"], CONFLICT_KINDS, `${where}.kind`);
    const resource = list(conflict["resource"], `${where}.resource`).map((node, j) =>
      onMap(text(node, `${where}.resource[${j}]`), `${where}.resource[${j}]`),
    );
    if (resource.length !== (kind === "vertex" ? 1 : 2)) {
      fail(`${where}.resource`, "a vertex conflict names one node, the others two");
    }
    const pair = list(conflict["robots"], `${where}.robots`).map((robot, j) =>
      text(robot, `${where}.robots[${j}]`),
    );
    const [a, b] = pair;
    if (pair.length !== 2 || a === undefined || b === undefined) {
      fail(`${where}.robots`, "expected two robots");
    }
    if (!robotIds.has(a) || !robotIds.has(b)) fail(`${where}.robots`, "unknown robot");
    const start = whole(conflict["start"], `${where}.start`);
    const end = whole(conflict["end"], `${where}.end`);
    if (end < start) fail(where, "end before start");
    return { kind, resource, robots: [a, b], start, end };
  });
  const total = whole(conflictsIn["total"], "conflicts.total");
  if (listed.length > total) fail("conflicts", "more are listed than counted");

  const tick_s = real(top["tick_s"], "trace.tick_s");
  if (tick_s <= 0) fail("trace.tick_s", "must be positive");

  let earlier = 0;
  const robot_events = (v2 ? list(top["robot_events"], "robot_events") : []).map(
    (item, i): RobotEvent => {
      const where = `robot_events[${i}]`;
      const event = object(item, where, ROBOT_EVENT_KEYS);
      const tick = whole(event["tick"], `${where}.tick`);
      if (tick < earlier || tick > end_tick) {
        fail(`${where}.tick`, "out of time order or outside the run");
      }
      earlier = tick;
      const kind = oneOf(event["kind"], ROBOT_EVENT_KINDS, `${where}.kind`);
      const robot = text(event["robot"], `${where}.robot`);
      if (!robotIds.has(robot)) fail(`${where}.robot`, `unknown robot ${robot}`);
      const has = (["target", "ticks", "level", "reason"] as const).filter(
        (name) => event[name] !== null,
      );
      const expected = EVENT_FIELDS[kind];
      if (has.length !== expected.length || has.some((name) => !expected.includes(name))) {
        fail(where, `a ${kind} event has the fields ${[...expected].sort().join(", ") || "none"}`);
      }
      const ticks = event["ticks"] === null ? null : whole(event["ticks"], `${where}.ticks`);
      if (ticks !== null && ticks <= 0) fail(`${where}.ticks`, "must be positive");
      const level = event["level"] === null ? null : whole(event["level"], `${where}.level`);
      if (level !== null && (battery_model === null || level > battery_model.capacity)) {
        fail(`${where}.level`, "a level needs a battery model, and is at most its capacity");
      }
      const reason = event["reason"] === null ? null : text(event["reason"], `${where}.reason`);
      if (reason !== null && reason !== "low" && reason !== "top_up") {
        fail(`${where}.reason`, "must be low or top_up");
      }
      return {
        tick,
        kind,
        robot,
        node: onMap(text(event["node"], `${where}.node`), `${where}.node`),
        target:
          event["target"] === null
            ? null
            : onMap(text(event["target"], `${where}.target`), `${where}.target`),
        ticks,
        level,
        reason,
      };
    },
  );
  checkEventPlaces(robot_events, robots, nodes, edgeKeys);

  return {
    format: TRACE_FORMAT,
    schema_version: version,
    source: text(top["source"], "trace.source"),
    run: object(top["run"], "run"),
    policy: text(top["policy"], "trace.policy"),
    traffic: flag(top["traffic"], "trace.traffic"),
    battery: flag(top["battery"], "trace.battery"),
    tick_s,
    end_tick,
    speed_mps: real(top["speed_mps"], "trace.speed_mps"),
    load_ticks: whole(top["load_ticks"], "trace.load_ticks"),
    unload_ticks: whole(top["unload_ticks"], "trace.unload_ticks"),
    map: { nodes, edges },
    robots,
    orders,
    events,
    outcome: {
      delivered: ids("delivered"),
      in_progress: ids("in_progress"),
      pending: ids("pending"),
      unreleased: ids("unreleased"),
    },
    counters: {
      breakdowns: count("breakdowns"),
      breakdown_ticks: count("breakdown_ticks"),
      replans: count("replans"),
      moves_out_of_the_way: count("moves_out_of_the_way"),
      deferred_assignments: count("deferred_assignments"),
    },
    kpis: scalars(top["kpis"], "kpis"),
    charging: top["charging"] === null ? null : scalars(top["charging"], "charging"),
    conflicts: { total, listed },
    battery_model,
    robot_events,
  };
}
