# The viewer

A browser viewer that replays runs simulated by the AMR Fleet Optimization Engine. It simulates
nothing and controls nothing: every position, activity, KPI and conflict on screen comes from a
run's replay trace, which the Python engine writes
([D33 and D34](../docs/DECISIONS.md), [algorithms, sections 17 and 18](../docs/algorithms.md)).
Everything is synthetic.

## Run it

From the repository root, on Windows PowerShell:

```powershell
.\scripts\dev.ps1
```

Or by hand, in two terminals:

```powershell
.venv\Scripts\amr-api.exe          # the engine's service, http://127.0.0.1:8000
```

```powershell
cd web
npm ci                             # once
npm run dev                        # http://127.0.0.1:5173
```

The development server forwards `/api` to the service. Set `AMR_API_URL` to use another address.

## Checks

```powershell
npm run lint        # ESLint
npm run typecheck   # TypeScript, strict
npm test            # Vitest
npm run build       # type check, then a production build into dist/
```

## Layout

| Path | Content |
|---|---|
| `src/lib/trace.ts` | The trace format, and a strict check of a trace before it is used |
| `src/lib/motion.ts` | Position and activity at any moment: the port of `viz/motion.py` |
| `src/lib/replay.ts` | A trace prepared for playback: tracks, orders by logged step, corridors, conflicts |
| `src/lib/playback.ts` | The playback clock: one playhead, kept within the run |
| `src/lib/api.ts` | The service's endpoints, and the form's limits read from its OpenAPI document |
| `src/lib/runForm.ts` | The run form: presets, validation, the request it sends |
| `src/lib/view.ts` | Map-to-screen maths: fit, zoom about a point, pan, scale bar |
| `src/render/` | Drawing the map and the fleet on a 2D canvas |
| `src/components/` | Map, playback bar, run panel, details |

## Tests against Python

`src/lib/motion.test.ts` reads the golden fixtures in `../tests/data/replay/`: three real runs,
each with positions and activities computed by the Python reference functions. The viewer must
reproduce them exactly. `scripts/make_replay_fixtures.py` writes the fixtures, and a Python test
fails if the committed files are out of date.

## Keyboard

| Key | Action |
|---|---|
| Space | Play or pause |
| Left, Right | Step 1 s (with Shift: 10 s) |
| `,` and `.` | Step one tick (0.1 s) |
| `[` and `]` | Previous or next logged event |
| Home, End | Start or end of the run |
| F, `+`, `-` | Fit the map, zoom in, zoom out |
| Esc | Clear the selection |
