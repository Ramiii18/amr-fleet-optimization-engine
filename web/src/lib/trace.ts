/**
 * The replay trace format (schema version 1), as the engine writes it.
 *
 * The format is defined in Python: `src/amr_optimizer/replay/trace.py` (D33). This file reads
 * it and checks it. Nothing here computes anything about a run: a trace is the whole truth,
 * and what it does not contain is not shown.
 *
 * Every time is an integer tick of `tick_s` seconds. A visit is `[node, arrive, depart]`; a
 * null departure is a hold (the robot stays with no planned departure).
 */

export const TRACE_FORMAT = "amr-replay-trace";
export const SUPPORTED_SCHEMA_VERSION = 1;

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
  schema_version: typeof SUPPORTED_SCHEMA_VERSION;
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

const OUTCOME = ["delivered", "in_progress", "pending", "unreleased"] as const;
const COUNTERS = [
  "breakdowns",
  "breakdown_ticks",
  "replans",
  "moves_out_of_the_way",
  "deferred_assignments",
] as const;

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
  if (head["schema_version"] !== SUPPORTED_SCHEMA_VERSION) {
    fail(
      "trace.schema_version",
      `version ${JSON.stringify(head["schema_version"])} is not supported; ` +
        `this viewer reads version ${SUPPORTED_SCHEMA_VERSION}`,
    );
  }
  const top = object(data, "trace", TOP);

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
    const robot = object(item, where, ["id", "visits"]);
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
    return { id, visits };
  });
  if (robots.length === 0) fail("robots", "a trace needs at least one robot");

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

  return {
    format: TRACE_FORMAT,
    schema_version: SUPPORTED_SCHEMA_VERSION,
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
  };
}
