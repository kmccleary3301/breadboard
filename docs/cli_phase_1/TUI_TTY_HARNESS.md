### Quick Grid Diff Mode

To compare two existing snapshots without driving the harness, pass `--diff-before` and `--diff-after`:

```bash
tsx scripts/repl_pty_harness.ts --diff-before artifacts/stress/<ts>/case/grid_snapshots/final.txt --diff-after artifacts/stress/<ts>/case/grid_snapshots/active.txt
```

Exit code 0 means identical grids, 2 means differences.

## Stress CI Contract Reference

- `npm run stress:ci` now enforces that every PTY capture ships the full VT grid stack (`pty_raw.ansi`, `pty_plain.txt`, `pty_frames.ndjson`, `grid_snapshots/{final,active}.txt`, `grid_snapshots/final_vs_active.diff`, `grid_deltas.ndjson`, `anomalies.json`, `timeline.{ndjson,summary.json}`, `timeline_flamegraph.txt`, `ttydoc.txt`, `case_info.json`). Controller cases must include `transcript.txt`, `cli.log`, `sse_events.txt`, `timeline.{ndjson,summary.json}`, `timeline_flamegraph.txt`, `ttydoc.txt`, `case_info.json`.
- Budgets default to `TTFT_BUDGET_MS=2500`, `SPINNER_BUDGET_HZ=12`, `MAX_TIMELINE_WARNINGS=1`, `MIN_SSE_EVENTS=0`, `MAX_ANOMALIES=0`. Override them per run via env vars if the backend is slower or you are triaging layout-ordering regressions.
- Reference console logs (`artifacts/stress_ci_contract/pass.log` and `fail.log`) show what success vs. a layout anomaly looks like with the new checks. When updating the harness or script runner, refresh those logs so docs always match the tooling output.
# TTY Harness for Interactive KyleCode REPL

This harness gives us a reproducible way to drive the Ink REPL through a pseudo-terminal so we can capture the exact output (including ANSI control codes) produced by `kyle repl`. It complements the controller-level JSON script runner by exercising the **real** line editor stack, which is essential for debugging key handling issues such as the current Backspace regression.

## When to Use
- Investigating bugs that only appear in the interactive terminal (Backspace, Ctrl+Backspace, arrow navigation, bracketed paste, etc.).
- Capturing a raw transcript so the engine/TUI devs can compare what the user sees against the internal transcript renderer.
- Reproducing modal/overlay problems when the Ink tree itself is misbehaving.

## Running the Harness
```bash
cd kylecode_cli_skeleton
npm run devtools:pty -- \
  --script scripts/backspace_repro.json \
  --config ../agent_configs/opencode_mock_c_fs.yaml \
  --base-url "http://127.0.0.1:9099" \
  --snapshots scripts/backspace_repro_output.txt \
  --raw-log artifacts/backspace_repro_raw.ansi \
  --plain-log artifacts/backspace_repro_plain.txt \
  --metadata artifacts/backspace_repro_metadata.json \
  --frame-log artifacts/backspace_repro_frames.ndjson \
  --manifest artifacts/backspace_repro.spectator.json
```

Options:
- `--script <path>`: JSON file with the pseudo-terminal steps (required).
- `--config <path>`: passes `--config` through to `kyle repl` so we can point at the mock config or a real engine config.
- `--base-url <url>`: overrides `KYLECODE_API_URL` for the spawned process (falls back to env default if omitted).
- `--snapshots <path>`: write stripped-ANSI snapshots to a text file for diffing later.
- `--raw-log <path>` / `--plain-log <path>`: optional full-buffer dumps (ANSI vs. stripped) in case you need to replay the exact frames.
- `--metadata <path>`: JSON blob describing terminal size, command, timeouts, and duration—useful when packaging artifacts.
- `--frame-log <path>`: NDJSON file containing each raw tty chunk + timestamp so you can faithfully re-run the animation in another terminal.
- `--manifest <path>`: single JSON file referencing every generated artifact plus hashed clipboard metadata—handy when sharing bundles.
- `--input-log <path>`: optional NDJSON log of every typed character and key press (timestamped) for replay/debugging.
- `--winch-script <path>`: optional JSON file describing SIGWINCH events (see **Winch Scripts** below). Use this for resize storms so the harness can resize the PTY while steps execute.
- `--cols` / `--rows`: resize the pseudo-terminal (defaults: 120×36).
- `--echo`: stream the raw ANSI output to the parent terminal while the harness runs.
- `KYLECODE_INPUT_DEBUG=1`: optional env flag that tells the LineEditor to log every `(input, key)` tuple to stderr; handy when hunting low-level key translation bugs.
- `KYLECODE_FAKE_CLIPBOARD="text"`: bypass system clipboards when testing Ctrl+V by forcing the editor to paste the provided string (useful on headless servers where clipboardy cannot read from the OS).

### Clipboard Helpers
- `--clipboard-text "..."`: inline payload injected via `KYLECODE_FAKE_CLIPBOARD`; the harness records its length + SHA-256 inside the manifest.
- `--clipboard-file path/to/payload.txt`: load payload from disk before launching the REPL (relative paths resolve against repo root).
  - Provide **only one** of `--clipboard-text` / `--clipboard-file` to avoid ambiguity.

All other CLI flags can be threaded through via `--cmd` if we need to run a different command than `node dist/main.js repl`.

## Script Format
Scripts mirror the earlier controller-level runner but operate on raw key presses:

| Action | Fields | Description |
| --- | --- | --- |
| `wait` | `ms` | Sleep for the requested milliseconds. |
| `type` | `text`, `typingDelayMs?` | Type literal text into the REPL (optionally with per-character delay). |
| `press` | `key`, `repeat?`, `delayMs?` | Send control keys (`enter`, `backspace`, `ctrl+backspace`, arrows, etc.). |
| `waitFor` | `text`, `timeoutMs?` | Poll the tty buffer until the text appears. |
| `snapshot` | `label`, `includeAnsi?` | Capture the current screen buffer (written to the snapshots file). |
| `log` | `message` | Emit a harness-side log for clarity while reading runs. |
| `resize` | `cols`, `rows`, `delayMs?` | Issue a SIGWINCH (`pty.resize`) to simulate the terminal width/height changing mid-run. |

See `scripts/backspace_repro.json` for a concrete example that types `testing backspace`, issues five Backspace presses, then records the before/after snapshots so we can confirm the bug.

### Winch Scripts

`--winch-script` accepts a JSON array (or `{ "events": [...] }`) describing autonomous resize events that run in parallel with the main key script:

```json
[
  { "delayMs": 400, "cols": 200, "rows": 36 },
  { "delayMs": 250, "cols": 90, "rows": 36 },
  { "delayMs": 220, "cols": 160, "rows": 30 }
]
```

Each entry waits `delayMs` (optional, defaults to 0) and then calls `pty.resize(cols, rows)`. The harness logs every resize to the input manifest and records aggregate stats (count, min/max cols/rows, burst duration) inside `pty_metadata.json` → `resizeStats`. Those stats flow into `timeline_summary.json` and `ttydoc.txt`, so `stress:ci` can flag regressions when resize storms behave differently.

`scripts/resize_storm_winch.json` is the reference script used by the `resize_storm` stress case; tweak it (or provide your own via `--winch-script`) whenever you need deterministic window storms.

## Output Artifacts
When `--snapshots` is set, the harness writes sections like this:

```
# after-type
…
# after-backspace
…
```

Each section is the stripped-ANSI terminal buffer, so the engine dev can inspect the raw prompt state with `less -R` or diff it against future runs. Setting `--echo` mirrors the raw ANSI stream live for quick visual verification.

## Workflow Tips
1. Launch the FastAPI mock bridge via `scripts/run_cli_bridge_mock.sh` (or point `--base-url` to a real engine URL).
2. Build the CLI (`npm run build`) so `dist/main.js` includes any latest fixes.
3. Run the harness with the scenario you care about, store the snapshot output next to the JSON script, and share that log when reporting regressions.
4. After implementing a fix, re-run the same script to confirm the snapshot changes in the expected way (diff old/new output files).
