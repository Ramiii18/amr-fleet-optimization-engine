/**
 * The run form: what the user typed, checked against what the service accepts.
 *
 * The limits come from the service's OpenAPI document (`limitsFromOpenApi`). The cross-field
 * rules repeat the service's own: delays, charging and breakdowns need traffic, and a recovery
 * policy needs breakdowns. The service checks everything again and has the last word.
 */

import {
  type Disturbance,
  type NumberLimit,
  type RequestLimits,
  type SimulationRequest,
  limitProblem,
} from "./api";

export interface DisturbanceForm {
  on: boolean;
  /** Percent of corridor entries, as typed. */
  sharePercent: string;
  minS: string;
  maxS: string;
}

export interface RunForm {
  map: string;
  policy: string;
  robots: string;
  ordersPerHour: string;
  durationS: string;
  seed: string;
  traffic: boolean;
  delays: DisturbanceForm;
  /** A charging policy, or "" for no battery model. */
  charging: string;
  breakdowns: DisturbanceForm;
  recovery: string;
}

export type Problems = Partial<Record<string, string>>;

function pick(preferred: string, available: readonly string[]): string {
  return available.includes(preferred) ? preferred : (available[0] ?? preferred);
}

export function defaultForm(
  limits: RequestLimits | null,
  maps: readonly string[],
  policies: readonly string[],
): RunForm {
  const value = (limit: NumberLimit | undefined, otherwise: number): string =>
    String(limit?.fallback ?? otherwise);
  return {
    map: pick(limits?.defaults.map ?? "large", maps),
    policy: pick(limits?.defaults.policy ?? "greedy", policies),
    robots: value(limits?.robots, 5),
    ordersPerHour: value(limits?.ordersPerHour, 60),
    durationS: value(limits?.durationS, 3600),
    seed: value(limits?.seed, 0),
    traffic: limits?.defaults.traffic ?? true,
    delays: { on: false, sharePercent: "10", minS: "1", maxS: "5" },
    charging: "",
    breakdowns: { on: false, sharePercent: "2", minS: "30", maxS: "180" },
    recovery: limits?.defaults.recovery ?? "wait",
  };
}

/** A named starting point for the form. Every preset is an ordinary request to the service. */
export interface Preset {
  id: string;
  label: string;
  hint: string;
  values: Partial<RunForm>;
}

export const PRESETS: readonly Preset[] = [
  {
    id: "quick",
    label: "Quick look",
    hint: "Small map, 3 robots, 10 minutes",
    values: {
      map: "small",
      policy: "greedy",
      robots: "3",
      ordersPerHour: "120",
      durationS: "600",
      seed: "1",
      traffic: true,
    },
  },
  {
    id: "fleet",
    label: "Fleet with traffic",
    hint: "Large map, 10 robots, planned around each other",
    values: {
      map: "large",
      policy: "cpsat-fair",
      robots: "10",
      ordersPerHour: "300",
      durationS: "1800",
      seed: "0",
      traffic: true,
    },
  },
  {
    id: "free-flow",
    label: "Free flow",
    hint: "No traffic management: the conflicts it would prevent",
    values: {
      map: "medium",
      policy: "greedy",
      robots: "6",
      ordersPerHour: "240",
      durationS: "900",
      seed: "2",
      traffic: false,
    },
  },
  {
    id: "breakdowns",
    label: "Breakdowns",
    hint: "Large map, 10 robots, breakdowns with replanning",
    values: {
      map: "large",
      policy: "insertion-fair",
      robots: "10",
      ordersPerHour: "200",
      durationS: "1800",
      seed: "3",
      traffic: true,
      breakdowns: { on: true, sharePercent: "2", minS: "30", maxS: "180" },
      recovery: "replan",
    },
  },
];

/** `preset` applied to a fresh default form. A map or policy the service does not offer is
 * replaced by one it does. */
export function applyPreset(
  preset: Preset,
  limits: RequestLimits | null,
  maps: readonly string[],
  policies: readonly string[],
): RunForm {
  const form = { ...defaultForm(limits, maps, policies), ...preset.values };
  return { ...form, map: pick(form.map, maps), policy: pick(form.policy, policies) };
}

function parse(textValue: string): number {
  const trimmed = textValue.trim();
  return trimmed === "" ? Number.NaN : Number(trimmed);
}

function check(
  problems: Problems,
  field: string,
  textValue: string,
  limit: NumberLimit | undefined,
): number {
  const value = parse(textValue);
  if (!Number.isFinite(value)) problems[field] = "Enter a number.";
  else if (limit !== undefined) {
    const problem = limitProblem(value, limit);
    if (problem !== null) problems[field] = problem;
  }
  return value;
}

function disturbance(
  problems: Problems,
  field: string,
  form: DisturbanceForm,
  limits: RequestLimits | null,
): Disturbance {
  const percentLimit: NumberLimit | undefined =
    limits === null
      ? undefined
      : { ...limits.share, min: limits.share.min * 100, max: limits.share.max * 100 };
  const sharePercent = check(problems, `${field}.share`, form.sharePercent, percentLimit);
  const min_s = check(problems, `${field}.min_s`, form.minS, limits?.disturbanceS);
  const max_s = check(problems, `${field}.max_s`, form.maxS, limits?.disturbanceS);
  if (Number.isFinite(min_s) && Number.isFinite(max_s) && max_s < min_s) {
    problems[`${field}.max_s`] ??= "Must be at least the minimum.";
  }
  return { share: sharePercent / 100, min_s, max_s };
}

/**
 * The request the form describes, and what is wrong with it. `request` is null while there is
 * any problem. A replay trace is always asked for: this viewer exists to play runs back.
 */
export function validateForm(
  form: RunForm,
  limits: RequestLimits | null,
): { request: SimulationRequest | null; problems: Problems } {
  const problems: Problems = {};
  if (form.map === "") problems["map"] = "Choose a map.";
  if (form.policy === "") problems["policy"] = "Choose a policy.";
  const robots = check(problems, "robots", form.robots, limits?.robots);
  const orders_per_hour = check(problems, "orders_per_hour", form.ordersPerHour, limits?.ordersPerHour);
  const duration_s = check(problems, "duration_s", form.durationS, limits?.durationS);
  const seed = check(problems, "seed", form.seed, limits?.seed);

  // Without traffic the service refuses delays, charging and breakdowns; the form switches
  // them off instead of sending a request that must fail.
  const delays = form.traffic && form.delays.on;
  const breakdowns = form.traffic && form.breakdowns.on;
  const request: SimulationRequest = {
    map: form.map,
    policy: form.policy,
    robots,
    orders_per_hour,
    duration_s,
    seed,
    traffic: form.traffic,
    delays: delays ? disturbance(problems, "delays", form.delays, limits) : null,
    charging: form.traffic && form.charging !== "" ? form.charging : null,
    breakdowns: breakdowns ? disturbance(problems, "breakdowns", form.breakdowns, limits) : null,
    recovery: breakdowns ? form.recovery : "wait",
    record_trace: true,
  };
  return { request: Object.keys(problems).length === 0 ? request : null, problems };
}

/** A finished job's request as a form, so that a run can be made again or changed. */
export function formOfRequest(request: SimulationRequest, base: RunForm): RunForm {
  const part = (value: Disturbance | null, fallback: DisturbanceForm): DisturbanceForm =>
    value === null
      ? { ...fallback, on: false }
      : {
          on: true,
          sharePercent: String(+(value.share * 100).toFixed(6)),
          minS: String(value.min_s),
          maxS: String(value.max_s),
        };
  return {
    map: request.map,
    policy: request.policy,
    robots: String(request.robots),
    ordersPerHour: String(request.orders_per_hour),
    durationS: String(request.duration_s),
    seed: String(request.seed),
    traffic: request.traffic,
    delays: part(request.delays, base.delays),
    charging: request.charging ?? "",
    breakdowns: part(request.breakdowns, base.breakdowns),
    recovery: request.recovery,
  };
}
