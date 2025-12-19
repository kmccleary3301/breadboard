### Manual Grid Diff CLI

Compare any two snapshots via:

```bash
cd kylecode_cli_skeleton
tsx scripts/grid_diff.ts artifacts/stress/<ts>/<case>/grid_snapshots/final.txt artifacts/stress/<ts>/<case>/grid_snapshots/active.txt
```

Non-zero exit indicates differences (useful when reviewing crash captures).
# KyleCode TUI Stress Playback Suite

> Purpose: capture reproducible playback scenarios that exercise the Ink modal stack, key router, and transcript batching changes. These scripts run through the normal `kyle repl --script …` entry point so engine/CLI devs can compare snapshots without driving the interactive UI.

## Automated Artifact Bundles

Use `npm run stress:bundle` to regenerate every scenario (controller + PTY) and drop self-contained archives under `kylecode_cli_skeleton/artifacts/stress/<timestamp>/`. Each case folder includes:

- Original script copy
- Playback transcript or PTY snapshots
- Raw ANSI + stripped logs
- Harness metadata (terminal cols/rows, watchdogs)
- Clipboard manifest (SHA-256 + payload size for paste/attachment cases)
- VT100 grid reconstructions (`grid_snapshots/final.txt` / `active.txt`), line-change deltas, ASCII diff (`grid_snapshots/final_vs_active.diff`), layout anomaly reports, stitched timelines (`timeline.ndjson` + `timeline_summary.json`), ASCII flamegraphs (`timeline_flamegraph.txt`), `ttydoc.txt` summaries, and `case_info.json` (id, script path/hash, config) for every scenario. Timelines include derived metrics such as `ttftSeconds`, `spinnerHz`, and `linesPerEvent`; `stress:ci` enforces budget defaults (TTFT ≤ 2.5 s, spinnerHz ≤ 12) whenever the metrics are available.

Command overview:

```bash
cd kylecode_cli_skeleton
npm run stress:bundle -- \
  --config ../agent_configs/opencode_cli_mock_guardrails.yaml \
  --base-url http://127.0.0.1:9099
```

Flags:

- `--case <id>` (repeatable): limit to specific cases such as `modal_overlay` or `ctrl_v_paste`.
- `--config <path>` / `--base-url <url>`: override the default mock config or FastAPI bridge URL.
- `--cmd <command>`: change the REPL executable (`node dist/main.js repl` by default) if you need to test an alternate build.
- `--out-dir <dir>`: place bundles in a custom directory (default: `artifacts/stress`).
- `--no-zip`: skip creating the `<timestamp>.zip` archive (useful for local debugging).

Each batch emits `artifacts/stress/<timestamp>/manifest.json` summarizing the cases and a sibling `<timestamp>.zip` so you can hand the bundle to another engineer. The PTY cases reuse the spectator harness internally, so the JSON manifest already references raw/clean/frame logs without additional steps.

When `npm run stress:ci` completes it also copies the latest zip to `artifacts/stress/stress_ci_latest.zip` so the CI job can upload a stable artifact name.

### Signals Captured Per Case
- **Controller runs** (`kind: repl`): `transcript.txt` (renderText snapshots), `cli.log` (stdout/stderr), and `sse_events.txt`. The SSE tap attaches live as soon as the session ID is printed; for very fast scripts you may only see a placeholder line because the queue drained before the tap connected.
- **PTY runs** (`kind: pty`): `pty_raw.ansi`, `pty_plain.txt`, `pty_frames.ndjson`, `pty_metadata.json`, `pty_snapshots.txt`, and `pty_manifest.json` with clipboard SHA-256 metadata.
- **Batch-level metrics**: `guardrail_metrics.jsonl` + `guardrail_metrics.summary.json` mirror the output of `../scripts/ci_guardrail_metrics.sh`. Pass `--guard-log <dir>` repeatedly to target specific logging directories, or rely on the script’s built-in defaults.

Bundle manifest example (`artifacts/stress/20251115-155459/manifest.json`):

```json
{
  "cases": [
    {
      "id": "mock_hello",
      "kind": "repl",
      "transcriptPath": ".../transcript.txt",
      "logPath": ".../cli.log",
      "ssePath": ".../sse_events.txt"
    }
  ],
  "guardrailMetrics": {
    "outputPath": ".../guardrail_metrics.jsonl",
    "summaryPath": ".../guardrail_metrics.summary.json"
  }
}
```

### CI Shortcut

`npm run stress:ci` is a thin wrapper around `stress:bundle` that launches the mock FastAPI bridge, runs a high-signal subset (modal overlay, layout ordering PTY probe, ctrl+V paste), verifies the emitted artifacts, and ensures guardrail metrics are produced. The script exits non-zero if:

- The FastAPI bridge fails to boot on `127.0.0.1:9099`
- Any scenario fails or omits required outputs (e.g., missing SSE log, PTY snapshots)
- Guardrail metrics cannot be generated

Use it in CI or as a smoke test before sharing bundles:

```bash
cd kylecode_cli_skeleton
npm run build
npm run stress:ci
```

### CI Contract & Budget Enforcement

The hardened gate now asserts a concrete artifact contract for every case plus runtime budgets. Each PTY case must emit the full VT100 set (`pty_raw.ansi`, `pty_plain.txt`, `pty_frames.ndjson`, `grid_snapshots/{final,active}.txt`, `grid_snapshots/final_vs_active.diff`, `grid_deltas.ndjson`, `anomalies.json`, `timeline.{ndjson,summary.json}`, `timeline_flamegraph.txt`, `ttydoc.txt`, `case_info.json`). Controller (`kind: repl`) cases must include `transcript.txt`, `cli.log`, `sse_events.txt`, `timeline.{ndjson,summary.json}`, `timeline_flamegraph.txt`, `ttydoc.txt`, and `case_info.json`. Missing files cause `stress:ci` to stop immediately with an actionable message.

Timeline summaries are fed through `tools/timeline/checkBudgets.mjs`, which enforces the default budgets below (override via env vars when necessary):

| Env var | Default | Effect |
| --- | --- | --- |
| `TTFT_BUDGET_MS` | `2500` | Reject cases whose `ttftSeconds` × 1000 exceed this many milliseconds. |
| `SPINNER_BUDGET_HZ` | `12` | Reject excessive PTY churn (events per second) when the spinner runs too fast. |
| `MIN_SSE_EVENTS` | `0` | Require at least N SSE entries (bump to `1` for live provider configs). |
| `MAX_TIMELINE_WARNINGS` | `1` | Allow at most this many warnings (currently we tolerate the `no-sse-events` placeholder). |
| `MAX_ANOMALIES` | `0` | Layout assertions fail once anomalies exceed this count (override temporarily only when triaging known issues). |
| `STRESS_CI_KEY_FUZZ_ITERATIONS` / `STRESS_CI_KEY_FUZZ_STEPS` / `STRESS_CI_KEY_FUZZ_SEED` | `1` / `60` / `0` | Control the built-in key-fuzz harness. Set iterations to `0` to skip locally; nightly jobs can bump the iteration count for deeper coverage while keeping the replay artifacts under `key_fuzz/`. |

`STRESS_CI_CASES="modal_overlay ctrl_v_paste" npm run stress:ci` lets you re-run a subset when you need a passing reference bundle. All CLI output from a successful run and a forced failure (layout anomalies) lives under `artifacts/stress_ci_contract/{pass.log,fail.log}` so future contributors can see the expected diagnostics.

> Tip: regenerate contracts whenever you touch the `stress:ci` script so docs/tests point at a real log. Keep the FastAPI mock bridge running (see `TUI_MOCK_BACKEND_PLAYBOOK.md`) or the gate will fail the moment it tries to create sessions. Key-fuzz logs live under `artifacts/stress/<ts>/key_fuzz/`—attach the `failure-*` directory whenever a fuzz iteration surfaces a regression.

### Deterministic Mock SSE Mode

When Ray/engine access is unavailable you can now replay deterministic SSE payloads directly through the stress harness. Any scenario that declares `mockSseScript` automatically triggers the lightweight Node mock (`tools/mock/mockSseServer.ts`), so no extra flags are needed. Today both `mock_hello` (simple completions) and `mock_multi_tool` (tool + diff stream) opt in.

Example (records fresh bundles under `artifacts/stress_mock_sse/<timestamp>/{mock_hello,mock_multi_tool}` without touching the FastAPI bridge):

```bash
cd kylecode_cli_skeleton
npm run build
npm run stress:bundle -- \
  --case mock_hello \
  --case mock_multi_tool \
  --no-zip \
  --out-dir artifacts/stress_mock_sse
```

Reference bundles are copied to `artifacts/stress_mock_sse/reference_mock/{mock_hello,mock_multi_tool}` for quick diffs. Pass `--no-case-mock-sse` if you need to force the real FastAPI bridge even when a scenario advertises a mock script.

The mock server replays the JSON file exactly, honors `--mock-sse-jitter-ms`, `--mock-sse-drop-rate`, etc., and automatically annotates `timeline_summary.json` with a `chaos` block so you know a deterministic stream was used. Adjust the host/port with `--mock-sse-host` / `--mock-sse-port` if you need to run multiple bundles in parallel.

### Chaos Metadata

Each case now records its chaos knobs in three places:

1. `timeline_summary.json.chaos` — includes the source (`bridge` vs. `mock-sse`), latency/jitter/drop settings, and script path when deterministic playback is active.
2. `case_info.json.chaos` — mirrors the summary so `stress:ci` can print the exact knobs beside guard metrics.
3. `ttydoc.txt` — new “Chaos Metadata” section reproduces the JSON so reviewers can see the settings without opening the summary manually.

`npm run stress:ci` logs a `[stress:ci] <case> chaos=…` line for every scenario, making it obvious when replayed bundles used jitter/delay fault injection. Reference the deterministic `ttydoc` outputs under `artifacts/stress_mock_sse/reference_mock/*/ttydoc.txt` and the mixed mock/resize examples under `kylecode_cli_skeleton/artifacts/stress_docs/chaos_ttydoc_examples/` (`mock_hello_ttydoc.txt`, `resize_storm_ttydoc.txt`) for examples.

### SSE Notes

SSE taps stream `/sessions/{id}/events` during controller-mode runs. When a script completes before the tap attaches, the harness writes a friendly fallback message (`[sse] Stream aborted after timeout…`) so the log is never empty. Treat these as valid captures—if you need real events, rerun the scenario with `--remote-stream` against a slower backend.

## Scenarios Covered

1. **Modal Overlay Thrash** — Opens `/models`, navigates beyond the viewport, confirms a model change, reopens the overlay, and escapes. This ensures the modal stack + responsive picker stay stable while streaming hints arrive.
2. **Hotkey Toggle Loop** — Uses `Ctrl-K` to toggle the overlay mid-stream and verifies the slash-command prompt remains editable after closing.
3. **Transcript Snapshot** — Emits `snapshot` actions after long tool/event bursts to ensure `<Static>` history and streaming tail render exactly once in recorded outputs.
4. **Resize storm PTY** — The new PTY script + winch script drive repeated `SIGWINCH` bursts (200↔80 cols plus row jitter) while the prompt streams tokens. Metadata + `timeline_summary.json` capture `resizeStats` so we can see how many resizes occurred and how wide the bursts were.
5. **Ctrl+V paste (small)** — PTY harness ensures regular clipboard pastes behave like live typing (no chip, supports undo/redo).
6. **Ctrl+V paste (large)** — PTY harness pastes a >1 KB payload to force `[Pasted Content …]` chips. `tools/assertions/clipboardChecks.ts` scans the captured snapshots and grid dumps; the bundle fails immediately if the placeholder disappears.
7. **Mock hello (deterministic SSE)** — Uses the per-case `mockSseScript` so the Node mock SSE bridge replays `scripts/mock_sse_sample.json` even when Ray is offline; the artifacts include the injected “chaos” metadata.
8. **Mock multi tool (deterministic SSE)** — Similar to `mock_hello` but replays `scripts/mock_sse_diff.json` so tool call/diff rendering can be tested deterministically (`scripts/mock_multi_tool_script.json` drives the request).
9. **Attachment submit** — PTY harness pastes a tiny PNG (via `KYLECODE_FAKE_CLIPBOARD=data:image/png,…`), confirms the `[Attachment N: image/png …]` chips appear, submits, and exercises `/attachments` + `/retry`. Clipboard assertions verify both generic attachment chips and the attachment metadata (bytes/mime/optional average color).
10. **Key-fuzz harness** — `stress:bundle` (and `stress:ci`) automatically runs a seeded PTY fuzz test via `scripts/run_key_fuzz.ts`; failures drop `key_fuzz/failure-XXX` directories with the replay steps and stack trace.
11. **Markdown rendering (assistant)** — `markdown_basic.json` requests headings/lists/inline code; snapshots validate rich markdown blocks and the `/view markdown` toggle.
12. **Diff rendering (assistant)** — `markdown_diff.json` asks for a fenced unified diff; snapshots should show red/green diff coloring and collapse summaries.

## Files

Use the mock backend instructions in `docs/cli_phase_1/TUI_MOCK_BACKEND_PLAYBOOK.md` (bridge launcher, env vars, logging) whenever real provider creds aren’t available.

| File | Description |
| --- | --- |
| `kylecode_cli_skeleton/scripts/modal_overlay_stress.json` | Drives `/models`, cursor thrash, and overlay close/reopen cycles with snapshots (60 s completion timeout to reduce flaky waits). |
| `kylecode_cli_skeleton/scripts/modal_overlay_stress_output.txt` / `_cli_shared_latest.txt` | Recorded frames for mock vs. shared-engine runs (the `_cli_shared_latest` files come from `agent_configs/opencode_cli_mock_guardrails.yaml`). |
| `kylecode_cli_skeleton/scripts/resize_storm.json` | PTY steps (type request, capture snapshots) used in tandem with `resize_storm_winch.json` to stress the layout under rapid SIGWINCH bursts. |
| `kylecode_cli_skeleton/scripts/resize_storm_winch.json` | Companion winch script that resizes the PTY between 80↔200 columns with slight row jitter; referenced automatically by the `resize_storm` case. |
| `kylecode_cli_skeleton/scripts/resize_storm_output.txt` / `_cli_shared_latest.txt` | Snapshot outputs for the resize run (mock + shared engine). |
| `kylecode_cli_skeleton/scripts/paste_flood.json` | Sends a large bracketed-paste payload to ensure the custom line editor handles single-shot updates. |
| `kylecode_cli_skeleton/scripts/paste_flood_output.txt` / `_cli_shared_latest.txt` | Snapshot outputs for the paste flood run (mock + shared engine). |
| `kylecode_cli_skeleton/scripts/token_flood.json` | Requests a verbose explanation to flood SSE events and exercise batching/live-slot updates. |
| `kylecode_cli_skeleton/scripts/token_flood_output.txt` / `_cli_shared_latest.txt` | Snapshot outputs for the token flood run (mock + shared engine). |
| `kylecode_cli_skeleton/scripts/mock_sse_guardrail.json` | Deterministic SSE script used when `token_flood` runs in mock mode; emits a guardrail interruption so CI can validate banner handling without hitting providers. |
| `kylecode_cli_skeleton/scripts/mock_sse_diff.json` | Deterministic SSE script used when `paste_flood` runs in mock mode; emits tool call/results and short diff previews. |
| `kylecode_cli_skeleton/scripts/ctrl_v_paste.json` | Uses the PTY harness + `KYLECODE_FAKE_CLIPBOARD` to assert Ctrl+V pastes clipboard text into the line editor. |
| `kylecode_cli_skeleton/scripts/ctrl_v_paste_output.txt` / `_cli_shared.txt` | Snapshot showing the pasted payload appended to the prompt (mock + real configs respectively). |
| `kylecode_cli_skeleton/scripts/ctrl_v_paste_large.json` (alias of `ctrl_v_paste.json`) | Reused script ID that pastes a >1 KB payload via `LARGE_CLIPBOARD_PAYLOAD` to force chip rendering; bundled under the `ctrl_v_paste_large` case. |
| `artifacts/stress_clipboard/<ts>/ctrl_v_paste_large/*` | Example PTY bundle demonstrating the new `[Pasted Content …]` assertion (latest run: `20251117-154118`). |
| `artifacts/stress_clipboard/<ts>/clipboard_manifests/*.json` | Per-case copies of `pty_manifest.json` so you can diff clipboard metadata (bytes, mime, average color) between runs without digging into every directory. |
| `artifacts/stress/<ts>/clipboard_diffs/*.json` | Auto-generated diff files (when a previous batch exists) that highlight per-case changes in length, bytes, mime, averageColor, and SHA-256 for clipboard payloads. |
| `kylecode_cli_skeleton/scripts/mock_sse_sample.json` | Deterministic SSE payload consumed by the mock bridge when `--case-mock-sse` (or `STRESS_CI_CASE_MOCK_SSE=1`) is set. |
| `kylecode_cli_skeleton/scripts/layout_ordering_pty.json` | PTY harness that sends a short prompt, waits for the transcript to log the user turn, and snapshots the header/body/footer ordering—used by `stress:ci` instead of the manual `resize_storm` script. |
| `kylecode_cli_skeleton/scripts/attachment_submit.json` | PTY harness scenario that pastes an image chip and submits it, verifying the FastAPI `/attachments` endpoint and CLI uploader. |
| `kylecode_cli_skeleton/scripts/attachment_submit_output_cli_shared.txt` | Snapshots for the attachment submit run (before submit + after submit) generated via `npm run devtools:pty` with `KYLECODE_FAKE_CLIPBOARD=data:image/png,...`. |
| `kylecode_cli_skeleton/scripts/layout_ordering_probe.json` | Helper script that waits longer (8 s) before snapshotting to inspect spinner/state ordering when providers are slow (`layout_ordering_probe_output.txt`). |
| `kylecode_cli_skeleton/scripts/markdown_basic.json` | Controller script requesting a markdown-rich reply (heading, list, inline code); use with `/view markdown on` to verify rich rendering. |
| `kylecode_cli_skeleton/scripts/markdown_diff.json` | Controller script requesting a fenced unified diff; checks red/green diff rendering and collapse summaries. |
| `artifacts/stress/<ts>/key_fuzz/{run.json,summary.json}` | Metadata + summary for each key-fuzz sweep; failures produce `failure-XXX/steps.json` for replay, and `summary.json` lists the run dir + failures for CI logs. |

> Collapsed transcript controls: when an assistant/tool reply exceeds ~20 lines (or contains large diffs) the snapshot shows the head/tail plus a summary line such as `… 84 lines hidden — Δ +42/-3 in src/foo.ts`. While driving the live TUI, use `[` / `]` to target a collapsed block (the footer shows the current selection) and press `e` to expand/collapse it; script mode always records the collapsed form to keep outputs deterministic.

> Tip: when regenerating `ctrl_v_paste` (or other clipboard scenarios) set `KYLECODE_FAKE_CLIPBOARD` to plain text or a `data:image/png;base64,...` URI to emulate the payload without touching the host clipboard.

> Layout sanity: `layout_ordering_pty_after.txt` captures the fixed header → transcript window → composer ordering. Run `npm run devtools:pty -- --script scripts/layout_ordering_pty.json --snapshots scripts/layout_ordering_pty_after.txt --config ../agent_configs/opencode_mock_c_fs.yaml --base-url http://127.0.0.1:9099` whenever you tweak the Ink layout so we keep the snapshot in sync with Claude Code.
> Log-window audit: `scripts/log_window_compact.txt` and `scripts/log_window_full.txt` are generated via `npm run stress:logwindow`. They render the same 250-turn transcript in compact vs. full scroll mode so you can diff exactly how many lines compact mode trims. Regenerate after changing `/view scroll` behavior.

> Visual polish: guardrail banners now surface in the snapshots, and assistant/tool blocks longer than ~20 lines show the `… lines hidden` helper line. If the layout changes, re-run `modal_overlay`, `resize_storm`, and `paste_flood` so the collapsed previews and colored diff lines stay deterministic.

> Clipboard manifests: run `diff artifacts/stress_clipboard/<old>/clipboard_manifests/attachment_submit.json artifacts/stress_clipboard/<new>/clipboard_manifests/attachment_submit.json` whenever attachment behavior changes. The files contain the raw `pty_manifest` metadata (`length`, `mime`, `bytes`, `averageColor`, SHA-256) so regressions surface as plain JSON diffs.
> When bundles are emitted under the same `--out-dir`, `clipboard_diffs/*.json` is produced automatically if a previous run exists. Inspect `artifacts/stress/<ts>/clipboard_diffs/<case>.json` before submitting changes that affect paste/attachment behavior.

## Known Issues

- **Layout ordering anomalies (resize storms):** `layout_ordering` still captures two anomalies where finalized assistant messages float above the header during aggressive resize storms. See the latest failure bundle under `kylecode_cli_skeleton/artifacts/stress/layout_ordering_failure/20251117-175701/` for full PTY grids, anomalies, clipboard metadata, and timeline output. Keep `layout_ordering` in `stress:ci` so regressions remain visible until the Ink layout fix lands.

## Running Locally

```bash
cd kylecode_cli_skeleton
npm run build
node dist/main.js repl \
  --config ../agent_configs/opencode_mock_c_fs.yaml \
  --script scripts/modal_overlay_stress.json \
  --script-output scripts/modal_overlay_stress_output.txt \
  --script-final-only \
  --script-max-duration-ms 180000
```

Or run the entire battery (including the PTY clipboard/attachment captures) via:

```bash
cd kylecode_cli_skeleton
npm run build
npm run stress:snapshots
```

To refresh the log-window fixtures only:

```bash
npm run stress:logwindow
```
Export `KYLECODE_API_URL=http://127.0.0.1:9099` (or whatever host/port you passed to the mock bridge) before running the CLI. The `--script-final-only` flag uses the new transcript renderer that omits streaming tails so the snapshot mirrors the final on-screen frame.

> 2025-11-12 update: `scripts/paste_flood_output.txt`, `scripts/modal_overlay_stress_output.txt`, `scripts/resize_storm_output.txt`, and `scripts/layout_ordering_pty_after.txt` were regenerated against the mock FastAPI bridge (`mock/dev`). Re-run the commands above with `--config ../agent_configs/opencode_mock_c_fs.yaml`, `--model mock/dev`, and `KYLECODE_API_URL=http://127.0.0.1:9099` whenever clipboard or layout changes land.  
> 2025-11-14 update: `scripts/modal_overlay_stress_output.txt`, `scripts/resize_storm_output.txt`, `scripts/ctrl_v_paste_output_cli_shared.txt`, and `scripts/attachment_submit_output_cli_shared.txt` capture the same scenarios against the new CLI-friendly configs (`opencode_openai_gpt5nano_c_fs_cli_shared.yaml`) with the real FastAPI bridge so guardrails/attachments reflect production behaviour.

> Script mode now enforces the same 180 s watchdog that the PTY harness uses. Reduce or raise it with `--script-max-duration-ms` when a scenario legitimately needs more (or less) time; the flag is optional because 180 s is the built-in default.

### PTY Harness Safeguards

When using the PTY harness (`npm run devtools:pty`) you can now rely on two guard rails so scripts never hang indefinitely:

- `--watchdog-ms <ms>` (default `60000`): aborts the run if the terminal produces no output for the specified duration. The harness sends `Ctrl+C`, kills the child, and surfaces `Watchdog timeout…`.
- `--submit-timeout-ms <ms>` (default `8000`): after a script presses Enter with pending input, the harness expects a new `USER` transcript line. If one does not appear before the timeout it raises `Prompt submission timeout…` so we can investigate why the prompt was never delivered.
- `--max-duration-ms <ms>` (default `180000`): hard cap on total harness runtime. If a script plus backend hangs beyond this window, the harness exits with `Harness timeout…` instead of stalling indefinitely.

Both options can be set to `0` to disable the respective safeguard. These checks run inside every polling loop (`wait`, `waitFor`, `type`, `press`) so even long waits will respect the timeouts.

### Troubleshooting: read_file on directories
- The backend only supports `read_file` on regular files. Asking the agent to read the workspace root (`/shared_folders/.../agent_ws_*`) will raise `IsADirectoryError`.
- Use `/files` (which prints the cached tree) or target specific files (`read_file README.md`) when you need context. The shared CLI transcripts (`coding_task_output_cli_shared*.txt`) follow this pattern to avoid crashes.

## Extending the Suite

1. Use the existing step types (`openModelMenu`, `press`, `snapshot`, `wait`, `log`) to choreograph new behaviors (e.g., `/files` storms, retry loops).
2. Prefer `snapshot` labels that describe the expected state (“models-scroll”, “overlay-closed”) so diffs are easy to reason about.
3. Store any generated outputs next to the script file to aid future comparisons (`scripts/<name>_output.txt`).

These scripts double as regression guards: whenever modal or transcript code changes, regenerate the outputs and verify only intentional differences appear.

## Guardrail Fixtures

To keep CLI guardrail behaviour in sync with OpenCode/Claude parity tests:

1. Run each stress script against the mock backend so that `kyle repl --script …` produces a full Logging V2 run under `logging/<timestamp>_cli_*`.
2. Invoke the replay harness with the CLI shared config to generate sanitized guard events:
   ```bash
   python scripts/replay_opencode_session.py \
     --config agent_configs/opencode_openai_gpt5nano_c_fs_cli_shared.yaml \
     --session misc/opencode_tests/protofs_todo_session.json \
     --workspace logging/cli_guardrail_replay_ws \
     --limit 400 \
     --guardrail-expected misc/opencode_tests/protofs_todo_session.cli_guardrails.json
   ```
3. Once a real CLI transcript is available, replace the placeholder `protofs_todo_session.cli_guardrails.json` with the guard events captured from the stress run (export the run’s `meta/run_summary.json` → `guardrail_events` array). Future replays will fail if the CLI config deviates from the recorded guard sequence, giving us parity coverage without re-running the full TUI harness.

### Current fixtures (2025-11-12)

| Scenario | Logging directory | Guard fixture |
| --- | --- | --- |
| Coding task CLI shared | `logging/20251112-054444_cli_guardrail_coding_task` | `misc/cli_guardrail_tests/coding_task_cli_shared.guardrails.json` |
| Multi-file CLI shared | `logging/20251112-054549_cli_guardrail_multi_file` | `misc/cli_guardrail_tests/multi_file_cli_shared.guardrails.json` |
| Modal overlay stress | `logging/20251112-054941_cli_guardrail_modal_overlay` | `misc/cli_guardrail_tests/modal_overlay_cli_shared.guardrails.json` |
| Resize storm stress | `logging/20251112-055115_cli_guardrail_resize_storm` | `misc/cli_guardrail_tests/resize_storm_cli_shared.guardrails.json` |

### Automated capture workflow

Use `scripts/run_cli_guardrail_suite.sh` to exercise every shared CLI scenario end-to-end:

```bash
# Rebuild CLI, replay coding_task/multi_file/modal_overlay/resize scripts, update fixtures + guard metrics.
scripts/run_cli_guardrail_suite.sh --update-fixtures

# CI mode: rerun scripts and fail if guardrail events diverge from committed fixtures.
scripts/run_cli_guardrail_suite.sh --verify-only
```

The suite spins up the FastAPI bridge per scenario, tolerates the known `waitFor timeout reached` exits on the stress tests, sanitizes guardrail events into `misc/cli_guardrail_tests/*.guardrails.json`, and emits `artifacts/cli_guardrail_metrics.jsonl` via `scripts/ci_guardrail_metrics.sh` so dashboards/CI can diff guard counters.

> Known issue: the CLI now retries `/sessions/{id}` for up to 30 s after an SSE timeout and the new watchdog keeps each run under 180 s, so the modal/resize scripts complete reliably. The only remaining `waitFor timeout reached` errors happen when the backend never streams any events (e.g., mock providers waiting for manual input). In that case inspect `logging/cli_bridge.log` and/or rerun with `KYLECODE_DEBUG_WAIT=1` to confirm `eventCount=0`.
>
> Layout-ordering PTY probe: still emits two layout anomalies because finalized assistant messages float above the header during aggressive resize storms. A frozen capture lives at `artifacts/stress/layout_ordering_failure/20251117-152135`. Until the UI bug is fixed, keep this case in `stress:ci` for visibility, or temporarily skip it via `STRESS_CI_CASES="modal_overlay ctrl_v_paste"` when you need a green run.
