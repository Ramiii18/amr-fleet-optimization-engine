/**
 * The playback clock of a recorded run.
 *
 * It owns one number, the playhead in ticks, and moves it between 0 and the end of the run. It
 * computes nothing about the run itself: where robots are at the playhead is read from the trace.
 * The playhead may fall between whole ticks, so that movement is smooth at any speed.
 */

import { clampTick } from "./motion";

/** Simulated seconds per real second. */
export const SPEEDS = [1, 2, 5, 10, 20, 60, 120] as const;
export const DEFAULT_SPEED = 10;

type Listener = () => void;

export class Playback {
  private tickValue = 0;
  private playingValue = false;
  private speedValue: number = DEFAULT_SPEED;
  private readonly listeners = new Set<Listener>();
  /** Changes whenever anything a listener can read changes. */
  version = 0;

  constructor(
    readonly endTick: number,
    readonly tickS: number,
  ) {}

  get tick(): number {
    return this.tickValue;
  }

  get playing(): boolean {
    return this.playingValue;
  }

  get speed(): number {
    return this.speedValue;
  }

  get seconds(): number {
    return this.tickValue * this.tickS;
  }

  get atEnd(): boolean {
    return this.tickValue >= this.endTick;
  }

  subscribe = (listener: Listener): (() => void) => {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  };

  private changed(): void {
    this.version += 1;
    for (const listener of this.listeners) listener();
  }

  /** Move the playhead; it stays within the run. */
  seek(tick: number): void {
    const next = clampTick(tick, this.endTick);
    if (next === this.tickValue) return;
    this.tickValue = next;
    this.changed();
  }

  /** Start playing. At the end of the run, playing starts again from the beginning. */
  play(): void {
    if (this.endTick <= 0) return;
    if (this.atEnd) this.tickValue = 0;
    this.playingValue = true;
    this.changed();
  }

  pause(): void {
    if (!this.playingValue) return;
    this.playingValue = false;
    this.changed();
  }

  toggle(): void {
    if (this.playingValue) this.pause();
    else this.play();
  }

  restart(): void {
    this.playingValue = false;
    this.tickValue = 0;
    this.changed();
  }

  setSpeed(speed: number): void {
    if (!(speed > 0) || speed === this.speedValue) return;
    this.speedValue = speed;
    this.changed();
  }

  /** Pause, then move by whole ticks from the nearest whole tick in that direction. */
  stepTicks(ticks: number): void {
    this.playingValue = false;
    const from = ticks > 0 ? Math.floor(this.tickValue) : Math.ceil(this.tickValue);
    this.tickValue = clampTick(from + ticks, this.endTick);
    this.changed();
  }

  stepSeconds(seconds: number): void {
    this.stepTicks(Math.round(seconds / this.tickS));
  }

  /** Advance by `elapsedMs` of real time while playing. Stops at the end of the run. */
  advance(elapsedMs: number): void {
    if (!this.playingValue || !(elapsedMs > 0)) return;
    const next = this.tickValue + ((elapsedMs / 1000) * this.speedValue) / this.tickS;
    if (next >= this.endTick) {
      this.tickValue = this.endTick;
      this.playingValue = false;
    } else {
      this.tickValue = next;
    }
    this.changed();
  }
}
