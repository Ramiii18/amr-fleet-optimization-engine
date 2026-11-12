/**
 * The engine's HTTP service (`amr-api`), as this viewer uses it.
 *
 * Every request goes to `/api`, which the development server forwards to the service. The
 * limits and choices of a simulation request are read from the service's own OpenAPI document,
 * so the form cannot drift from what the backend accepts.
 */

import type { Scalar, TraceEdge, TraceNode } from "./trace";

const BASE: string = import.meta.env?.VITE_API_BASE ?? "/api";

/** A request the service refused, or could not be reached for (`status` 0). */
export class ApiError extends Error {
  override name = "ApiError";
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
  }
}

export interface MapSummary {
  name: string;
  nodes: number;
  edges: number;
}

export interface MapDetail {
  name: string;
  nodes: TraceNode[];
  edges: TraceEdge[];
}

export interface Disturbance {
  share: number;
  min_s: number;
  max_s: number;
}

export interface SimulationRequest {
  map: string;
  policy: string;
  robots: number;
  orders_per_hour: number;
  duration_s: number;
  seed: number;
  traffic: boolean;
  delays: Disturbance | null;
  charging: string | null;
  breakdowns: Disturbance | null;
  recovery: string;
  record_trace: boolean;
}

export interface SimulationOutcome {
  kpis: Record<string, Scalar>;
  charging: Record<string, Scalar> | null;
  breakdowns: number;
  replans: number;
  /** Wall-clock seconds the run took in the worker process. Depends on the machine. */
  compute_s: number;
}

export type JobStatus = "queued" | "running" | "done" | "failed";
export type TraceStatus = "none" | "pending" | "available" | "evicted" | "too_large";

export interface Job {
  id: string;
  status: JobStatus;
  submitted_at: string;
  request: SimulationRequest;
  result: SimulationOutcome | null;
  error: string | null;
  trace: TraceStatus;
}

/** The reason in a FastAPI error body: a string, or a list of field problems (422). */
export function errorDetail(body: unknown): string | null {
  if (typeof body !== "object" || body === null) return null;
  const detail = (body as { detail?: unknown }).detail;
  if (typeof detail === "string") return detail;
  if (!Array.isArray(detail)) return null;
  const problems = detail.map((item: unknown) => {
    if (typeof item !== "object" || item === null) return String(item);
    const { loc, msg } = item as { loc?: unknown; msg?: unknown };
    const where = Array.isArray(loc) ? loc.filter((part) => part !== "body").join(".") : "";
    const message = typeof msg === "string" ? msg.replace(/^Value error, /, "") : "invalid";
    return where ? `${where}: ${message}` : message;
  });
  return problems.join("; ");
}

async function call(path: string, init?: RequestInit): Promise<Response> {
  let response: Response;
  try {
    response = await fetch(`${BASE}${path}`, init);
  } catch {
    throw new ApiError(0, "The engine's service cannot be reached. Is amr-api running on port 8000?");
  }
  if (response.ok) return response;
  let body: unknown = null;
  try {
    body = await response.json();
  } catch {
    // Not JSON: the proxy answers this way when the service is down.
  }
  const detail = errorDetail(body);
  if (detail === null && response.status >= 500) {
    throw new ApiError(
      response.status,
      "The engine's service did not answer. Is amr-api running on port 8000?",
    );
  }
  throw new ApiError(response.status, detail ?? `The service answered ${response.status}.`);
}

async function getJson<T>(path: string, signal?: AbortSignal): Promise<T> {
  const response = await call(path, { signal });
  return (await response.json()) as T;
}

export const api = {
  health: (signal?: AbortSignal) =>
    getJson<{ status: string; version: string; tick_s: number }>("/health", signal),
  maps: (signal?: AbortSignal) => getJson<MapSummary[]>("/maps", signal),
  map: (name: string, signal?: AbortSignal) =>
    getJson<MapDetail>(`/maps/${encodeURIComponent(name)}`, signal),
  policies: (signal?: AbortSignal) => getJson<string[]>("/policies", signal),
  openApi: (signal?: AbortSignal) => getJson<unknown>("/openapi.json", signal),
  jobs: (signal?: AbortSignal) => getJson<Job[]>("/simulations", signal),
  job: (id: string, signal?: AbortSignal) =>
    getJson<Job>(`/simulations/${encodeURIComponent(id)}`, signal),
  async submit(request: SimulationRequest, signal?: AbortSignal): Promise<Job> {
    const response = await call("/simulations", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(request),
      signal,
    });
    return (await response.json()) as Job;
  },
  /**
   * The trace of a finished job, as parsed JSON. The service sends it gzip-compressed with
   * `Content-Encoding: gzip`; the browser decompresses it before this code sees it.
   */
  trace: (id: string, signal?: AbortSignal) =>
    getJson<unknown>(`/simulations/${encodeURIComponent(id)}/trace`, signal),
};

// --- what the service accepts, read from its OpenAPI document ---------------------------------

export interface NumberLimit {
  /** Smallest accepted value; with `exclusive`, the value must be greater than it. */
  min: number;
  exclusive: boolean;
  max: number;
  fallback: number | null;
  integer: boolean;
}

export interface RequestLimits {
  robots: NumberLimit;
  ordersPerHour: NumberLimit;
  durationS: NumberLimit;
  seed: NumberLimit;
  share: NumberLimit;
  disturbanceS: NumberLimit;
  charging: string[];
  recovery: string[];
  defaults: { map: string; policy: string; traffic: boolean; recovery: string };
}

type Schema = Record<string, unknown>;

function asSchema(value: unknown): Schema | null {
  return typeof value === "object" && value !== null ? (value as Schema) : null;
}

function numberLimit(schema: Schema | null): NumberLimit | null {
  if (schema === null) return null;
  const { minimum, exclusiveMinimum, maximum, type } = schema;
  const exclusive = typeof exclusiveMinimum === "number";
  const min = exclusive ? exclusiveMinimum : minimum;
  if (typeof min !== "number" || typeof maximum !== "number") return null;
  return {
    min,
    exclusive,
    max: maximum,
    fallback: typeof schema["default"] === "number" ? schema["default"] : null,
    integer: type === "integer",
  };
}

function choices(schema: Schema | null): string[] | null {
  if (schema === null) return null;
  const direct = schema["enum"];
  if (Array.isArray(direct)) return direct.filter((v): v is string => typeof v === "string");
  const anyOf = schema["anyOf"];
  if (Array.isArray(anyOf)) {
    for (const option of anyOf) {
      const found = choices(asSchema(option));
      if (found !== null) return found;
    }
  }
  return null;
}

/** The limits of `POST /simulations`, or null if the document does not have the expected shape
 * (the form then leaves validation to the service, which always has the last word). */
export function limitsFromOpenApi(document: unknown): RequestLimits | null {
  const schemas = asSchema(asSchema(asSchema(document)?.["components"])?.["schemas"]);
  const request = asSchema(asSchema(schemas?.["SimulationRequest"])?.["properties"]);
  const disturbance = asSchema(asSchema(schemas?.["Disturbance"])?.["properties"]);
  if (request === null || disturbance === null) return null;
  const property = (name: string): Schema | null => asSchema(request[name]);
  const robots = numberLimit(property("robots"));
  const ordersPerHour = numberLimit(property("orders_per_hour"));
  const durationS = numberLimit(property("duration_s"));
  const seed = numberLimit(property("seed"));
  const share = numberLimit(asSchema(disturbance["share"]));
  const disturbanceS = numberLimit(asSchema(disturbance["max_s"]));
  const charging = choices(property("charging"));
  const recovery = choices(property("recovery"));
  if (
    robots === null ||
    ordersPerHour === null ||
    durationS === null ||
    seed === null ||
    share === null ||
    disturbanceS === null ||
    charging === null ||
    recovery === null
  ) {
    return null;
  }
  const fallback = <T>(name: string, kind: string, otherwise: T): T => {
    const value = property(name)?.["default"];
    return typeof value === kind ? (value as T) : otherwise;
  };
  return {
    robots,
    ordersPerHour,
    durationS,
    seed,
    share,
    disturbanceS,
    charging,
    recovery,
    defaults: {
      map: fallback("map", "string", ""),
      policy: fallback("policy", "string", ""),
      traffic: fallback("traffic", "boolean", true),
      recovery: fallback("recovery", "string", recovery[0] ?? "wait"),
    },
  };
}

/** Why `value` is outside `limit`, or null if it is inside. */
export function limitProblem(value: number, limit: NumberLimit): string | null {
  if (!Number.isFinite(value)) return "Enter a number.";
  if (limit.integer && !Number.isInteger(value)) return "Enter a whole number.";
  if (limit.exclusive ? value <= limit.min : value < limit.min) {
    return limit.exclusive ? `Must be greater than ${limit.min}.` : `Must be at least ${limit.min}.`;
  }
  if (value > limit.max) return `Must be at most ${limit.max}.`;
  return null;
}
