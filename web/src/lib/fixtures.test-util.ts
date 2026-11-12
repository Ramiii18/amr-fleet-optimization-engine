/**
 * The golden fixtures written by Python (`scripts/make_replay_fixtures.py`).
 *
 * Each is a real run's trace and, for every robot, rows of `[tick, x, y, activity]` computed
 * by the Python reference functions. The tests here must reproduce the rows from the trace
 * alone. A Python test keeps the committed files current.
 */

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

export const FIXTURE_NAMES = ["free_flow", "cut_short", "disturbed"] as const;
export type FixtureName = (typeof FIXTURE_NAMES)[number];

const FOLDER = new URL("../../../tests/data/replay/", import.meta.url);

function read(file: string): unknown {
  return JSON.parse(readFileSync(fileURLToPath(new URL(file, FOLDER)), "utf8"));
}

export function traceJson(name: FixtureName): unknown {
  return read(`${name}.trace.json`);
}

export type SampleRow = [tick: number, x: number, y: number, activity: string];

export function samples(name: FixtureName): Record<string, SampleRow[]> {
  const data = read(`${name}.samples.json`) as {
    format: string;
    version: number;
    columns: string[];
    robots: Record<string, SampleRow[]>;
  };
  if (data.format !== "amr-replay-samples" || data.version !== 1) {
    throw new Error(`${name}: unexpected samples format`);
  }
  if (data.columns.join() !== "tick,x_m,y_m,activity") {
    throw new Error(`${name}: unexpected sample columns`);
  }
  return data.robots;
}
