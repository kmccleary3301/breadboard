# TUI Testing Toolkit Expansion Plan

## Context
- The UX overhaul (M1–M3) is essentially complete, but regressions still surface because our validation tools only capture controller-level transcripts and coarse log outputs.
- We need parity with what the user sees in tmux: raw terminal frames, attachment payloads, guardrail telemetry, and zipped artifacts per run so bugs can be reproduced from bash scripts alone.
- Existing assets:
  - JSON script runner (`kyle repl --script …`)
  - PTY harness (`npm run devtools:pty`) for targeted cases (e.g., Backspace)
  - Log-window renderer + guardrail metrics script
- Gaps: no always-on PTY capture for stress suites, no automatic artifact bundles, no CI integration, limited clipboard/attachment auditing.

## Current Tooling Inventory (as of 2025-02-14)
- **Script directory** (`kylecode_cli_skeleton/scripts/`): ~70 scenario JSON files (`modal_overlay_stress.json`, `resize_storm.json`, `paste_flood.json`, etc.) with paired `*_output*.txt` snapshots. Coverage focuses on layout, attachments, and guardrails but lacks PTY-level artifacts.
- **Harness commands**:
  - `npm run stress:snapshots` ⇒ `scripts/run_stress_snapshots.sh` uses the controller-level runner + `renderText` to dump transcripts; does not capture terminal frames or SSE logs.
  - `npm run stress:logwindow` ⇒ `scripts/generate_log_window_fixture.ts` snapshots the event-log widget only.
  - `npm run devtools:pty` ⇒ `scripts/repl_pty_harness.ts` accepts key sequences for a *single* repro, no batching.
- **Guardrail tooling**: `npm run guardrails:metrics` (wraps `../scripts/ci_guardrail_metrics.sh`) scrapes `logging/<timestamp>/meta/run_summary.json` to emit `artifacts/cli_guardrail_metrics.{jsonl,summary.json}`.
- **Artifacts today**: plain-text transcripts plus guard summaries; no zipped bundles, SSE event dumps, or clipboard manifests.
- **Docs**: `TUI_TTY_HARNESS.md` explains manual PTY usage; `TUI_STRESS_TESTS.md` lists scripts but not their missing signals; no single checklist tying everything together.

## Goals
1. **Observability** – Capture the *exact* Ink render cycle, SSE stream, and input bus for every scripted run.
2. **Artifact Packaging** – Produce zipped bundles with scripts, raw ANSI frames, cleaned transcripts, guard metrics, and attachment manifests.
3. **Automation & CI** – One command should regenerate every artifact locally, and CI should fail when artifacts drift or scripts hang.
4. **Documentation & Handoff** – Engineers must be able to run, inspect, and share these artifacts without extra context.

## Workstreams & Milestones

### WS1 – Inventory & Instrumentation (Day 0-0.5)
- [x] Audit current scripts/tools and document signal coverage. *(See “Current Tooling Inventory” section above.)*
- [x] Standardize naming for scripts vs. outputs vs. logs. *(See “File Naming Standard” below.)*

### WS2 – Spectator Harness (Day 0.5-2)
- [x] Extend `npm run devtools:pty` into a reusable module (`scripts/harness/spectator.ts`).
- [x] Accept JSON input bus, feed keystrokes via node-pty, and capture:
  - Raw ANSI stream per frame *(frame log written via `--frame-log`)*
  - Clean text snapshots (`--snapshots`)
  - Metadata (timestamp, cols/rows, watchdog) via `--metadata`
- [x] Support clipboard payload injection + hashing for auditing. *(Use `--clipboard-text` / `--clipboard-file`; SHA-256 stored in manifest.)*
- [x] Emit `.spectator.json` with references to all produced files. *(Produced via `--manifest` flag.)*

### WS3 – Artifact Bundles (Day 2-3)
- [x] Create `npm run stress:bundle` that iterates over `scripts/*.json`, runs spectator harness + controller runner, and outputs `artifacts/stress/<timestamp>/<case>/…` with a batch manifest.
- [x] Zip each run automatically (`zip -r artifacts/stress/<timestamp>.zip …`) unless `--no-zip` is provided.
- [x] Include SSE taps, guard metrics, attachment manifests, and clipboard hashes. *(SSE taps attach live; very short runs may fall back to a placeholder if the queue drains before the tap connects.)*
  - Bundles also emit `clipboard_diffs/*.json` and a batch-level `clipboard_diff_report.txt`, plus per-case `timeline_summary.json` with a `chaos` block and optional `resizeStats` used by the budget checker.

### WS4 – CI Integration (Day 3-4)
- [x] Provide `npm run stress:ci` that spins up the mock FastAPI bridge, runs a selected subset (modal overlay, layout ordering PTY, ctrl+V paste, mock SSE), and uploads artifacts/guard metrics.
- [x] Fail the job if any script times out, required artifacts are missing, SSE events stay below the configured minimum, or timeline/guard metrics drift beyond budgets (TTFT/spinnerHz/warnings/resize).
- [x] Update `docs/cli_phase_1/TUI_STRESS_TESTS.md` with the workflow + troubleshooting (including `stress:ci` instructions + SSE/resize/chaos notes). The gate now also writes `timeline_budget_warnings.jsonl` when violations occur so CI logs can be machine-parsed later.

### WS5 – Documentation & Handoff (Day 4)
- [ ] Summarize the toolkit in `UX_OVERHAUL_PLAN.md` + new doc section.
- [ ] Provide a quickstart snippet for future engineers.

## Acceptance Criteria
- Every stress script produces a .zip containing: script, PTY raw log, cleaned transcript, SSE log, guard metrics, attachment manifest, and summary JSON.
- `npm run stress:bundle` regenerates everything locally without manual intervention.
- CI command runs at least the critical subset (modal, resize, paste, mock SSE) and fails fast on hangs, missing artifacts, or budget violations (TTFT, spinnerHz, warnings, resize burst/events, guard metrics).
- Docs explain how to add new scripts, inspect artifacts, and share them with engine devs, including how to interpret `timeline_budget_warnings.jsonl` and `clipboard_diff_report.txt` when tests fail.

## Execution Notes
- Start with a single script (e.g., `scripts/modal_overlay_stress.json`) to prove out the spectator harness, then fan out to the rest.
- Keep CLI changes minimal; focus on external tooling around the existing `kyle repl --script` command.
- Ensure the harness cleans up sessions (call DELETE) even if the CLI hangs or fails.

- Deterministic SSE/token playback is provided by `tools/mock/mockSseServer.ts` (see `docs/cli_phase_1/MOCK_SSE_SPEC.md` for the full schema and flag reference). Each entry in `scripts/mock_sse_sample.json` (and future chaos scripts) uses `{ "delayMs": number, "event": <SessionEvent> }`. The server exposes `/sessions`, `/sessions/{id}/input`, `/sessions/{id}/events`, `/sessions/{id}` DELETE, and `/health`, so the CLI can point at it via `KYLECODE_API_URL` just like the FastAPI bridge.
- `npm run stress:bundle -- --case mock_hello --no-zip --out-dir artifacts/stress_mock_sse` now spins up the mock server automatically because `mock_hello` declares `mockSseScript`. Use `--mock-sse-host`, `--mock-sse-port`, `--mock-sse-jitter-ms`, `--mock-sse-drop-rate`, etc., to tweak latency and packet loss. Chaos metadata is recorded in `timeline_summary.json` (`chaos` block) and in each `case_info.json`.
- The latest deterministic captures live under `artifacts/stress_mock_sse/reference_mock/{mock_hello,mock_multi_tool}` so regressions can be diffed without re-running providers.
- Flag summary:
  - `--mock-sse-script <path>`: JSON payload to replay (overrides per-case scripts).
  - `--mock-sse-host <ip>` / `--mock-sse-port <port>`: bind address; default `127.0.0.1:9191`.
  - `--mock-sse-loop`: loop events forever so multiple `/input` calls reuse the same data.
  - `--mock-sse-delay-multiplier`, `--mock-sse-jitter-ms`, `--mock-sse-drop-rate`: coarse knobs for latency, jitter, and packet loss percentages.
  - `--case-mock-sse`: force-enable per-scenario mock scripts (default behaviour).
  - `--no-case-mock-sse`: disable automatic per-case mock playback when you need to hit the real FastAPI bridge.
- Open questions before promoting the mock into the default CI suite:
  - How should we encode alternating JSON/text payloads (some providers stream text fragments, others emit tool-call JSON)? Current format assumes JSON objects only.
  - Do we need a declarative way to describe guardrail interruptions (e.g., injecting `policy_violation` events mid-stream) so CLI guard fixtures stay realistic?
  - Should the mock expose `/attachments` and `/files` to unblock attachment playback, or is it sufficient to keep those as no-ops during chaos testing?
  - What’s the best way to version-control long mock scripts—single repo file per scenario, or zipped fixture packs within `tools/mock/scripts/`?

## File Naming Standard
- **Scripts**: `scripts/<scenario>.json` (controller) and `scripts/<scenario>_pty.json` when PTY-specific. Always keep names snake_case without spaces.
- **Repl outputs**: `scripts/<scenario>_output_<config>.txt` where `<config>` indicates `cli_shared`, `mock`, etc. Example: `modal_overlay_stress_cli_shared_latest.txt`.
- **TTY artifacts**: `artifacts/stress/<timestamp>/<case>/pty_*` for raw/clean logs plus a `pty_manifest.json`.
- **Batch manifests/archives**: `artifacts/stress/<timestamp>/manifest.json` with accompanying `<timestamp>.zip` at the same level. Each case folder includes the original script copy + per-run files so bundles remain self-contained.
