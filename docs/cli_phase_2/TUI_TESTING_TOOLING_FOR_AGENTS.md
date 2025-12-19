# TUI Testing & Tooling for Terminal-Only Agents

This document defines the **testing + interaction tooling contract** for the KyleCode TUI so that a terminal-only text agent (e.g., Codex/ChatGPT) can **debug, observe, reproduce, and regression-test** the REPL exactly as a human would see it.

If a new UX feature can’t be exercised and observed via these tools, treat it as **not shippable**.

---

## 1) Core Goal: “Give The Agent Eyes”

Terminal UIs are only “debuggable by an LLM” if we can produce **high-fidelity, deterministic artifacts**:

- what the user would see (screen contents, overlays, scrollback)
- what the program believed internally (state transitions)
- what the backend emitted (SSE events / errors)
- what inputs occurred (keys, resizes, clipboard/paste)
- timelines + budgets (TTFT, spinner cadence, resize storms)

The repository already contains most of this infrastructure; this doc standardizes how to use and extend it.

---

## 2) Existing Infrastructure (Current “Eyes & Radar” Stack)

### 2.1 Deterministic backend streams (mock SSE)
- Mock SSE server: `kylecode_cli_skeleton/tools/mock/replaySse.ts`
- CLI wrapper: `kylecode_cli_skeleton/tools/mock/mockSseServer.ts`
- Scripts: `kylecode_cli_skeleton/scripts/mock_sse_*.json`

Use this to reproduce tricky UI behavior without provider variance.

Notes:
- Mock scripts can be either a raw step array, or an object `{ "steps": [...], "files": { ... } }` to provide deterministic `/sessions/:id/files` listings for UI features like the `@` file picker.

### 2.2 Bridge launcher (engine-side)
- Bridge + optional mock SSE launcher: `scripts/run_cli_bridge_mock.sh`

This is the default entrypoint for CI/stress bundles.

### 2.3 Scriptable REPL runs (non-PTY)
- Script mode: `kylecode_cli_skeleton/src/commands/repl/scriptRunner.ts`
- Entrypoint: `kylecode_cli_skeleton/src/commands/repl/command.tsx`
- Produces: transcript output, `cli.log`, and an SSE capture tap in stress runs.

Useful for “logic/flow” validation, but it is not a full-fidelity terminal view.

### 2.4 PTY harness (full-fidelity terminal capture)
- Harness driver: `kylecode_cli_skeleton/scripts/repl_pty_harness.ts`
- Core implementation: `kylecode_cli_skeleton/scripts/harness/spectator.ts`

Capabilities:
- spawns the real TUI in a PTY (true terminal behavior)
- sends keys / types / waits / resizes / clipboard payloads
- captures raw ANSI (`pty_raw.ansi`), plain text (`pty_plain.txt`), frame stream (`pty_frames.ndjson`)
- takes scripted snapshots (`pty_snapshots.txt`)

### 2.5 Reconstruct the actual “screen” (VT grid)
- Grid reconstruction: `kylecode_cli_skeleton/tools/tty/vtgrid.ts`
- Grid diffing: `kylecode_cli_skeleton/tools/tty/gridDiff.ts`

This is how a text agent can “see” the screen layout deterministically.

### 2.6 Assertions + anomaly detection
- Layout assertions: `kylecode_cli_skeleton/tools/assertions/layoutChecks.ts`
- Clipboard assertions: `kylecode_cli_skeleton/tools/assertions/clipboardChecks.ts`

### 2.7 Timeline + budgets (performance + stability)
- Timeline builder: `kylecode_cli_skeleton/tools/timeline/buildTimeline.ts`
- Budget gate: `kylecode_cli_skeleton/tools/timeline/checkBudgets.mjs`
- Flamegraphs: `kylecode_cli_skeleton/tools/timeline/flamegraph.ts`

### 2.8 “One-file story” reports
- Per-case: `kylecode_cli_skeleton/tools/reports/ttydoc.ts` → `ttydoc.txt`
- Batch summary: `kylecode_cli_skeleton/tools/reports/ttydocBatch.ts`

These reports are the fast path for an agent to debug failures without opening a terminal UI.

### 2.9 Stress suite orchestration + CI gate
- Bundle runner: `kylecode_cli_skeleton/scripts/run_stress_bundles.ts`
- CI wrapper: `kylecode_cli_skeleton/scripts/run_stress_ci.sh`

Artifacts live under `kylecode_cli_skeleton/artifacts/stress/<timestamp>/<case>/…`.

Notable parity cases (run locally via `npm run stress:bundle -- --case <id> --no-zip`):
- `permission_rewind` (PTY): permission approval modal + rewind picker end-to-end (mock SSE + real keypresses).
- `file_picker` (PTY): `@` file picker modal navigation + mention insertion (mock `/files` + real keypresses).
- `file_mentions` (PTY): `@` pick file → submit → attached file context with truncation gating (mock `/files` + read modes).

---

## 3) Artifact Contract (What Every UI Bug Needs)

### 3.1 PTY case artifacts (layout-sensitive features)
Required (enforced by stress runner):
- `pty_raw.ansi`, `pty_plain.txt`, `pty_frames.ndjson`, `pty_metadata.json`, `pty_manifest.json`
- `input_log.ndjson` (keys/resizes/paste actions)
- `grid_snapshots/active.txt`, `grid_snapshots/final.txt`, `grid_snapshots/final_vs_active.diff`
- `grid_snapshots/normal_active.txt`, `grid_snapshots/normal_final.txt` (normal screen buffer)
- `grid_snapshots/alt_active.txt`, `grid_snapshots/alt_final.txt` (alternate screen buffer, e.g. Ctrl+T viewer)
- `grid_deltas.ndjson`, `anomalies.json`
- `timeline.ndjson`, `timeline_summary.json`, `timeline_flamegraph.txt`
- `repl_state.ndjson` (semantic state snapshots)
- `ttydoc.txt`, `case_info.json`

These let an agent answer: “what did the user see, and why did it break?”

### 3.2 REPL-script case artifacts (logic-sensitive features)
Required:
- `transcript.txt`, `cli.log`, `sse_events.txt`
- `repl_state.ndjson`
- `timeline.ndjson`, `timeline_summary.json`, `timeline_flamegraph.txt`
- `ttydoc.txt`, `case_info.json`

---

## 4) The Debugging Loop (Agent-Friendly)

Default rule: **mock-first, PTY-first**.

1. Reproduce with a deterministic script (mock SSE or scripted REPL).
2. Capture PTY artifacts if the bug is visual/layout/interaction.
3. Read `ttydoc.txt` first, then inspect:
   - `grid_snapshots/active.txt` (what was “on screen”)
   - `anomalies.json` (what invariant broke)
   - `timeline_summary.json` (TTFT/spinner/resize budgets)
   - `pty_plain.txt` / `pty_raw.ansi` (raw details)
4. Fix one bug.
5. Re-run the smallest case, then the CI subset.

---

## 5) How To Add A New Claude-Code-Parity Feature Without Going Blind

For each feature, add **at least one** of:

### 5.1 A mock SSE script (deterministic backend stimulus)
Add `kylecode_cli_skeleton/scripts/mock_sse_<feature>.json` that emits the exact event pattern the UI must handle.

### 5.2 A PTY script (deterministic user interaction)
Add `kylecode_cli_skeleton/scripts/<feature>.json` that presses keys/resizes/pastes and snapshots.

### 5.3 An assertion (deterministic verdict)
Add/update a checker under `kylecode_cli_skeleton/tools/assertions/` so a failure becomes a readable, structured error.

Then:
- register the case in the stress bundle runner (see `kylecode_cli_skeleton/scripts/run_stress_bundles.ts`)
- add it to the CI subset if it guards a core invariant

---

## 6) Recommended Upgrades (To Make Agents Even More Effective)

These are the “next 20%” that removes most remaining blind spots.

### 6.1 Semantic state dumps per snapshot (highest ROI)
Today, agents can reconstruct the screen, but some bugs require knowing internal state (selected model index, modal stack, collapse targeting).

The stress runner now emits `repl_state.ndjson` per case (gated by `KYLECODE_STATE_DUMP_PATH` in the TUI process).

Each entry should include:
- a serialized `ReplState` snapshot
- the “top layer” focus (editor/palette/modal)
- current session id/config/model/mode

### 6.2 Always emit a single-copy repro command
Write a `repro.sh` (or embed in `case_info.json`) that replays the exact run with the right env vars, config, and script paths.

### 6.3 Persist input logs for PTY runs
The PTY harness already supports `onInput`; the stress runner now writes `input_log.ndjson` so agents can see “what keys happened” without inference.

### 6.4 Tighten “VT grid” coverage as needed
If a new UI feature relies on ANSI sequences not currently modeled in `kylecode_cli_skeleton/tools/tty/vtgrid.ts`, add support and a regression test.

### 6.5 Private state dumps (`repl_state.ndjson`)
Every PTY/script stress case also writes a sanitized `repl_state.ndjson` next to the other artifacts. The state dump is gated by `KYLECODE_STATE_DUMP_PATH` (with `KYLECODE_STATE_DUMP_MODE=summary` and a `RATE_MS` throttle) so the TUI emits:
  - `timestamp`, `reason` (`change`/`final`)
  - a summarized `ReplState` (session id, pending response, guardrail notice, model menu stats, view prefs, completion flags, etc.)
  - minimal diffs of vast collections (tool logs, live slots, hints) so the file stays manageable

This is the “private state format” we need to parse if we want a terminal-only agent to know what the controller believes about model selection, modal overlays, or guardrail decisions. The harness already drops the file in `artifacts/stress/<batch>/<case>/repl_state.ndjson`, so add a lightweight parser that:
  1. Streams the NDJSON entries while the run is still live.
  2. Normalizes the `state` object keys into a readable report.
  3. Answers questions like “which modal is open?” or “what model menu item is current?” without needing to parse the raw transcript.

Mark this plan in docs—`repl_state.ndjson` is the private state format we are committing to parse before claiming Claude Code parity.

We now provide `scripts/observe_repl_state.ts` (and `npm run observe:repl-state -- --file <path>`) as the first parser/observer. It prints a running summary of status, pending responses, model menu, guardrail notices, and conversation/tool counts so the agent can “see” the controller-only view without opening a terminal.

---

## 7) How This Prevents “Browser Project Blindness”

Unlike browser UIs, terminal UIs can be made fully observable by:
- capturing raw terminal bytes (PTY)
- reconstructing the terminal screen deterministically (VT grid)
- producing machine-checkable invariants (assertions + budgets)

This turns interactive UX work into deterministic engineering work that a text agent can do end-to-end.

## 13) Live tmux bridge (prototype)
- `scripts/start_tmux_repl.sh` spins up a fixed-size tmux pane, wires a FIFO, and launches `breadboard`/Claude/Codex while emitting `repl_state.ndjson`.
- `scripts/agent_input_bus.ts` listens on that FIFO, parses literal or JSON keystrokes, and injects them into tmux so the agent can drive the exact same UI you see in the terminal (e.g. `{"keys":"Hello","enter":true}`); “submit” is intentionally sent as a distinct tmux injection to avoid paste-chunk heuristics in some TUIs.
- See `docs/cli_phase_2/TUI_INTERACTIVE_TOOLING_BRIDGE.md` for the full design and the next steps for using this live bridge.

