import { useMemo, useRef } from "react";

import { usePlaybackState, usePlayhead } from "../hooks";
import { clock } from "../lib/format";
import { type Playback, SPEEDS } from "../lib/playback";
import { type Replay, neighbourTick } from "../lib/replay";

interface Props {
  replay: Replay;
  playback: Playback;
}

/** At most this many marks are drawn on the timeline; more would merge into a solid band. */
const MAX_MARKS = 400;

export function PlaybackBar({ replay, playback }: Props) {
  const { playing, speed } = usePlaybackState(playback);
  const tick = usePlayhead(playback, 30);
  const trackRef = useRef<HTMLDivElement>(null);
  const { endTick, tickS } = replay;
  const share = endTick > 0 ? tick / endTick : 0;

  const conflictMarks = useMemo(() => {
    if (endTick <= 0) return [];
    const step = Math.max(1, Math.ceil(replay.conflicts.length / MAX_MARKS));
    return replay.conflicts
      .filter((_, index) => index % step === 0)
      .map((conflict) => ({
        left: (conflict.start / endTick) * 100,
        width: Math.max(((conflict.end - conflict.start) / endTick) * 100, 0.15),
      }));
  }, [replay.conflicts, endTick]);

  const seekFromPointer = (clientX: number): void => {
    const track = trackRef.current;
    if (track === null) return;
    const box = track.getBoundingClientRect();
    playback.seek(((clientX - box.left) / box.width) * endTick);
  };

  const jump = (direction: 1 | -1): void => {
    const target = neighbourTick(replay.eventTicks, playback.tick, direction);
    if (target !== null) {
      playback.pause();
      playback.seek(target);
    }
  };

  return (
    <div className="playback" role="group" aria-label="Playback">
      <div className="playback-buttons">
        <button type="button" className="icon" onClick={() => playback.restart()} title="Back to the start (Home)" aria-label="Restart">
          <svg viewBox="0 0 16 16" aria-hidden="true">
            <path d="M3 2.5v11M13 3 5.5 8l7.5 5z" />
          </svg>
        </button>
        <button type="button" className="icon" onClick={() => jump(-1)} title="Previous logged event ([)" aria-label="Previous event">
          <svg viewBox="0 0 16 16" aria-hidden="true">
            <path d="M8 3 2.5 8 8 13zM13.5 3 8 8l5.5 5z" />
          </svg>
        </button>
        <button type="button" className="icon" onClick={() => playback.stepSeconds(-1)} title="Back 1 s (←)" aria-label="Step back one second">
          <svg viewBox="0 0 16 16" aria-hidden="true">
            <path d="M10.5 3 5 8l5.5 5z" />
          </svg>
        </button>
        <button
          type="button"
          className="icon play"
          onClick={() => playback.toggle()}
          title={playing ? "Pause (Space)" : "Play (Space)"}
          aria-label={playing ? "Pause" : "Play"}
        >
          <svg viewBox="0 0 16 16" aria-hidden="true">
            {playing ? <path d="M4 2.5h3v11H4zM9 2.5h3v11H9z" /> : <path d="M4.5 2.5 13 8l-8.500 5.500z" />}
          </svg>
        </button>
        <button type="button" className="icon" onClick={() => playback.stepSeconds(1)} title="Forward 1 s (→)" aria-label="Step forward one second">
          <svg viewBox="0 0 16 16" aria-hidden="true">
            <path d="M5.5 3 11 8l-5.5 5z" />
          </svg>
        </button>
        <button type="button" className="icon" onClick={() => jump(1)} title="Next logged event (])" aria-label="Next event">
          <svg viewBox="0 0 16 16" aria-hidden="true">
            <path d="M8 3l5.5 5L8 13zM2.500 3 8 8l-5.500 5z" />
          </svg>
        </button>
      </div>

      <div className="playback-time" aria-live="off">
        <span className="now">{clock(tick * tickS)}</span>
        <span className="total">/ {clock(endTick * tickS)}</span>
      </div>

      <div
        className="timeline"
        ref={trackRef}
        onPointerDown={(event) => {
          event.currentTarget.setPointerCapture(event.pointerId);
          seekFromPointer(event.clientX);
        }}
        onPointerMove={(event) => {
          if (event.buttons !== 0 && event.currentTarget.hasPointerCapture(event.pointerId)) {
            seekFromPointer(event.clientX);
          }
        }}
      >
        <div className="timeline-rail">
          <div className="timeline-done" style={{ width: `${share * 100}%` }} />
          {conflictMarks.map((mark, index) => (
            <span key={index} className="timeline-conflict" style={{ left: `${mark.left}%`, width: `${mark.width}%` }} />
          ))}
        </div>
        <div className="timeline-thumb" style={{ left: `${share * 100}%` }} />
        <input
          type="range"
          className="timeline-input"
          aria-label="Simulated time"
          aria-valuetext={`${clock(tick * tickS)} of ${clock(endTick * tickS)}`}
          min={0}
          max={endTick}
          step={1}
          value={Math.round(tick)}
          onChange={(event) => playback.seek(Number(event.target.value))}
        />
      </div>

      <label className="playback-speed">
        <span>Speed</span>
        <select value={speed} onChange={(event) => playback.setSpeed(Number(event.target.value))}>
          {SPEEDS.map((value) => (
            <option key={value} value={value}>
              {value}×
            </option>
          ))}
        </select>
      </label>
    </div>
  );
}
