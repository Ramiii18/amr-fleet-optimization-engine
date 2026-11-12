import { useEffect, useMemo, useSyncExternalStore } from "react";

import type { Playback } from "./lib/playback";

/** Drives the playback clock from the screen's frames while a run is loaded. */
export function usePlaybackClock(playback: Playback | null): void {
  useEffect(() => {
    if (playback === null) return;
    let frame = 0;
    let last: number | null = null;
    const loop = (time: number): void => {
      frame = requestAnimationFrame(loop);
      // After a pause in rendering (a hidden tab), do not jump ahead by the whole gap.
      const elapsed = last === null ? 0 : Math.min(time - last, 100);
      last = time;
      playback.advance(elapsed);
    };
    frame = requestAnimationFrame(loop);
    return () => cancelAnimationFrame(frame);
  }, [playback]);
}

const NOTHING = (): (() => void) => () => undefined;

/** Whether the run is playing, and at what speed: these change rarely. */
export function usePlaybackState(playback: Playback | null): { playing: boolean; speed: number } {
  const playing = useSyncExternalStore(playback?.subscribe ?? NOTHING, () => playback?.playing ?? false);
  const speed = useSyncExternalStore(playback?.subscribe ?? NOTHING, () => playback?.speed ?? 1);
  return { playing, speed };
}

/**
 * The playhead in ticks, refreshed at most `perSecond` times a second.
 *
 * The map reads the playhead on every frame by itself. Panels of text do not need 60 updates a
 * second, and would cost far more than the canvas if they got them.
 */
export function usePlayhead(playback: Playback | null, perSecond = 10): number {
  const store = useMemo(() => throttledPlayhead(playback, perSecond), [playback, perSecond]);
  return useSyncExternalStore(store.subscribe, store.read);
}

function throttledPlayhead(
  playback: Playback | null,
  perSecond: number,
): { subscribe: (listener: () => void) => () => void; read: () => number } {
  if (playback === null) return { subscribe: NOTHING, read: () => 0 };
  let shown = playback.tick;
  return {
    read: () => shown,
    subscribe: (listener) => {
      let pending: number | null = null;
      let lastUpdate = 0;
      const flush = (): void => {
        pending = null;
        lastUpdate = performance.now();
        shown = playback.tick;
        listener();
      };
      const unsubscribe = playback.subscribe(() => {
        // A change while paused (a seek, a step) shows at once; while playing it is throttled.
        const wait = playback.playing ? 1000 / perSecond - (performance.now() - lastUpdate) : 0;
        if (wait <= 0) {
          if (pending !== null) window.clearTimeout(pending);
          flush();
        } else if (pending === null) {
          pending = window.setTimeout(flush, wait);
        }
      });
      shown = playback.tick;
      return () => {
        unsubscribe();
        if (pending !== null) window.clearTimeout(pending);
      };
    },
  };
}
