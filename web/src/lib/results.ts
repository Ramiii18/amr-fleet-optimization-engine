/**
 * Experiment results: the file written by `python -m amr_optimizer.lab --out`.
 *
 * The page shows what the file says and computes nothing new: every mean, interval and verdict
 * is the lab's own. A file that is not a results file, or whose figures contradict each other,
 * is refused instead of being shown half right.
 */

export class ResultsFormatError extends Error {}

export const VERDICTS = ["better", "worse", "equivalent", "inconclusive"] as const;
export type Verdict = (typeof VERDICTS)[number];

export interface ResultFamily {
  name: string;
  map: string;
  robots: number;
  ordersPerHour: number;
}

/** One policy against the baseline on one KPI, paired by seed. `null` is "not defined". */
export interface ResultContrast {
  kpi: string;
  family: string;
  policy: string;
  seeds: number;
  meanVariant: number;
  meanBaseline: number;
  /** Policy minus baseline. */
  difference: number;
  low: number | null;
  high: number | null;
  bootstrapLow: number | null;
  bootstrapHigh: number | null;
  wins: number;
  losses: number;
  ties: number;
  signedRankP: number | null;
  /** Only the primary KPI is corrected for the number of comparisons. */
  holmP: number | null;
  correlation: number | null;
  varianceRatio: number | null;
  margin: number;
  verdict: Verdict;
}

export interface LabResults {
  name: string;
  commit: string | null;
  model: string;
  baseline: string;
  policies: string[];
  primaryKpi: string;
  margin: number;
  confidence: number;
  seeds: number;
  families: ResultFamily[];
  runs: number;
  /** Runs that left orders undelivered. */
  undelivered: number;
  /** Runs that lasted longer than their disturbance schedule. */
  beyondHorizon: number;
  contrasts: ResultContrast[];
}

type Json = Record<string, unknown>;

function fail(what: string): never {
  throw new ResultsFormatError(what);
}

function object(value: unknown, where: string): Json {
  if (typeof value !== "object" || value === null || Array.isArray(value)) fail(`${where} must be an object`);
  return value as Json;
}

function list(value: unknown, where: string): unknown[] {
  if (!Array.isArray(value)) fail(`${where} must be a list`);
  return value;
}

function text(from: Json, key: string, where: string): string {
  const value = from[key];
  if (typeof value !== "string" || value === "") fail(`${where}.${key} must be a non-empty string`);
  return value;
}

function number(from: Json, key: string, where: string): number {
  const value = from[key];
  if (typeof value !== "number" || !Number.isFinite(value)) fail(`${where}.${key} must be a number`);
  return value;
}

function count(from: Json, key: string, where: string): number {
  const value = number(from, key, where);
  if (!Number.isInteger(value) || value < 0) fail(`${where}.${key} must be a whole number, zero or more`);
  return value;
}

function optional(from: Json, key: string, where: string): number | null {
  return from[key] === null || from[key] === undefined ? null : number(from, key, where);
}

function parseContrast(raw: unknown, index: number, families: Set<string>, policies: Set<string>): ResultContrast {
  const where = `analysis[${index}]`;
  const from = object(raw, where);
  const variant = text(from, "variant", where);
  const cut = variant.lastIndexOf("/");
  const family = variant.slice(0, cut);
  const policy = variant.slice(cut + 1);
  if (cut < 0 || !families.has(family)) fail(`${where}.variant names no family of the experiment: ${variant}`);
  if (!policies.has(policy)) fail(`${where}.variant names no policy of the experiment: ${variant}`);
  const verdict = text(from, "verdict", where);
  if (!(VERDICTS as readonly string[]).includes(verdict)) fail(`${where}.verdict is unknown: ${verdict}`);
  const contrast: ResultContrast = {
    kpi: text(from, "kpi", where),
    family,
    policy,
    seeds: count(from, "seeds", where),
    meanVariant: number(from, "mean_variant", where),
    meanBaseline: number(from, "mean_baseline", where),
    difference: number(from, "difference", where),
    low: optional(from, "low", where),
    high: optional(from, "high", where),
    bootstrapLow: optional(from, "bootstrap_low", where),
    bootstrapHigh: optional(from, "bootstrap_high", where),
    wins: count(from, "wins", where),
    losses: count(from, "losses", where),
    ties: count(from, "ties", where),
    signedRankP: optional(from, "signed_rank_p", where),
    holmP: optional(from, "holm_p", where),
    correlation: optional(from, "correlation", where),
    varianceRatio: optional(from, "variance_ratio", where),
    margin: number(from, "margin", where),
    verdict: verdict as Verdict,
  };
  if (contrast.wins + contrast.losses + contrast.ties !== contrast.seeds) {
    fail(`${where}: wins, losses and ties do not add up to the ${contrast.seeds} seeds`);
  }
  if ((contrast.low === null) !== (contrast.high === null)) fail(`${where}: the interval has one end only`);
  if (contrast.low !== null && contrast.high !== null) {
    if (contrast.low > contrast.difference || contrast.difference > contrast.high) {
      fail(`${where}: the difference lies outside its own interval`);
    }
  }
  const stated = contrast.meanVariant - contrast.meanBaseline;
  if (Math.abs(stated - contrast.difference) > 1e-6 * Math.max(1, Math.abs(stated))) {
    fail(`${where}: the difference is not the policy's mean minus the baseline's`);
  }
  return contrast;
}

/** Read a results file, or throw `ResultsFormatError` with the reason. */
export function parseResults(raw: unknown): LabResults {
  const root = object(raw, "the file");
  if (root.format !== "amr-experiment-results") fail("this is not an experiment results file");
  if (root.version !== 1) fail(`results version ${String(root.version)} is not supported (this viewer reads 1)`);
  const experiment = object(root.experiment, "experiment");
  const families = list(experiment.families, "experiment.families").map((item, index): ResultFamily => {
    const where = `experiment.families[${index}]`;
    const from = object(item, where);
    return {
      name: text(from, "name", where),
      map: text(from, "map", where),
      robots: count(from, "robots", where),
      ordersPerHour: number(from, "orders_per_hour", where),
    };
  });
  const policies = list(experiment.policies, "experiment.policies").map((item) =>
    typeof item === "string" ? item : fail("experiment.policies must be names"),
  );
  const baseline = text(experiment, "baseline", "experiment");
  if (!policies.includes(baseline)) fail("the baseline is not one of the experiment's policies");
  const rows = list(root.rows, "rows").map((item, index) => object(item, `rows[${index}]`));
  const seeds = list(experiment.seeds, "experiment.seeds").length;
  if (rows.length !== families.length * policies.length * seeds) {
    fail(`the file holds ${rows.length} runs, not one per family, policy and seed`);
  }
  const familyNames = new Set(families.map((family) => family.name));
  const policyNames = new Set(policies);
  const contrasts = list(root.analysis, "analysis").map((item, index) =>
    parseContrast(item, index, familyNames, policyNames),
  );
  for (const contrast of contrasts) {
    if (contrast.seeds !== seeds) fail(`a comparison uses ${contrast.seeds} seeds, the experiment ${seeds}`);
  }
  return {
    name: text(experiment, "name", "experiment"),
    commit: typeof root.commit === "string" ? root.commit : null,
    model: text(object(experiment.disturbances, "experiment.disturbances"), "model", "experiment.disturbances"),
    baseline,
    policies,
    primaryKpi: text(experiment, "primary_kpi", "experiment"),
    margin: number(experiment, "margin", "experiment"),
    confidence: number(experiment, "confidence", "experiment"),
    seeds,
    families,
    runs: rows.length,
    undelivered: rows.filter((row) => row.all_delivered !== true).length,
    beyondHorizon: rows.filter((row) => row.beyond_horizon === true).length,
    contrasts,
  };
}

/** The KPIs of the file, the primary one first, each with its comparisons in file order. */
export function byKpi(results: LabResults): { kpi: string; primary: boolean; contrasts: ResultContrast[] }[] {
  const groups = new Map<string, ResultContrast[]>();
  for (const contrast of results.contrasts) {
    const group = groups.get(contrast.kpi);
    if (group === undefined) groups.set(contrast.kpi, [contrast]);
    else group.push(contrast);
  }
  return [...groups]
    .map(([kpi, contrasts]) => ({ kpi, primary: kpi === results.primaryKpi, contrasts }))
    .sort((a, b) => Number(b.primary) - Number(a.primary));
}

export interface Scale {
  min: number;
  max: number;
}

/** One axis for a group of comparisons: it holds every interval, zero and the margin. */
export function scaleOf(contrasts: readonly ResultContrast[], margin: number): Scale {
  let min = -margin;
  let max = margin;
  for (const contrast of contrasts) {
    min = Math.min(min, contrast.low ?? contrast.difference);
    max = Math.max(max, contrast.high ?? contrast.difference);
  }
  const pad = (max - min) * 0.04 || 1;
  return { min: min - pad, max: max + pad };
}

/** Where a value sits on the axis, from 0 (left) to 1 (right). */
export function along(value: number, scale: Scale): number {
  return (value - scale.min) / (scale.max - scale.min);
}

/** A difference with its sign, as the lab's own report prints it. */
export function signed(value: number, digits = 1): string {
  const shown = value.toFixed(digits);
  if (Number(shown) === 0) return shown.replace("-", "");
  return value > 0 ? `+${shown}` : shown.replace("-", "−");
}
