# Test Tooling Plan – Checklist

This checklist tracks the execution of the planner’s recommendations. Keep it in sync as tasks complete. Grouped by milestone (Near/Mid/Long term). Use `[ ]` → `[x]` to mark progress.

## Near Term (Foundational Observability)

### VT Decoder & Screen Reconstruction
- [x] Implement VT100/ANSI decoder (`tools/tty/vtgrid.ts`) that ingests `pty_frames.ndjson` + `pty_metadata.json` and emits grid snapshots.
- [x] Generate `grid_snapshots/` and `grid_deltas.ndjson` artifacts during `stress:bundle`.
- [x] Add ASCII diff CLI (`scripts/grid_diff.ts`) and automatically emit `grid_snapshots/final_vs_active.diff` for PTY runs.

### UI Assertion DSL & Structural Checks
- [x] Define DSL/schema for assertions (header pinned, banner uniqueness, composer pinned, model table columns, etc.).
- [x] Implement assertion runner (`tools/assertions/layoutChecks.ts`) that consumes grid snapshots + metadata and outputs `anomalies.json`.
- [x] Wire assertions into `stress:bundle`/`stress:ci` and fail when anomalies exist.

### Reporting, Chaos, and Harness Ergonomics
- [x] Generate `ttydoc.txt` per case (timeline summary, flamegraph, snapshots/diffs) and enforce existence in `stress:ci`.
- [x] Record `case_info.json` (script path/hash, config) and expose `stress_ci_latest.zip` for CI artifacts.
- [x] Extend mock SSE server + bundler flags for latency/jitter/drop chaos; persist chaos info in timeline summaries.
- [x] Enhance PTY harness with per-key logging (`--input-log`) and grid diff mode (`--diff-before/--diff-after`).

### Timeline Stitching
- [x] Build timeline stitcher that aligns PTY deltas and SSE events into `timeline.ndjson`.
- [x] Include derived metrics (line-change counts, bytes, event counts, warnings) in `timeline_summary.json`.
- [x] Surface summary artifacts in case manifests/CI (required files in `stress:ci`).

### CI Gate Enhancements
- [x] Extend `npm run stress:ci` to fail on missing derived artifacts (grid snapshots, anomalies, timeline). *(See `scripts/run_stress_ci.sh` REQUIRED lists.)*
- [x] Enforce guard metric budgets (TTFT, spinnerHz, etc.) with configurable thresholds. *(`scripts/run_stress_ci.sh` pipes summaries through `tools/timeline/checkBudgets.mjs`.)*
- [x] Document new CI expectations in `TUI_STRESS_TESTS.md` / Quick Start.

## Current Execution Slice (Next ~10–15% Scope)

These are the next tightly scoped items to execute before moving deeper into the mid-term automation milestones. Complete them in order; each item should end with updated docs + artifacts.

1. **CI Gate Hardening Pass**
   - [x] Enumerate the artifact contract in `scripts/run_stress_ci.sh` (grid snapshots, `grid_deltas.ndjson`, `anomalies.json`, `timeline.ndjson`, `timeline_summary.json`, `case_info.json`, `ttydoc.txt`, guard metrics). Add explicit filesystem checks with actionable error messages.
   - [x] Update `scripts/run_stress_bundles.ts` to exit non-zero when any case lacks these files; bubble failures up through `npm run stress:ci`.
   - [x] Capture before/after runs (one expected pass, one forced failure) and store logs under `artifacts/stress_ci_contract/` for reference in docs.

2. **Guard Metric Budget Enforcement**
   - [x] Extend `tools/timeline/analyze.ts` (or add a new helper) to read `timeline_summary.json` and compare TTFT/spinnerHz/event-counts against env-configurable budgets.
   - [x] Teach `stress:ci` to pass `TTFT_BUDGET_MS`, `SPINNER_BUDGET_HZ`, and `MAX_ANOMALIES` env vars through to the analyzer; exit with descriptive failures when exceeded.
   - [x] Document default budgets, override examples, and troubleshooting steps in `docs/cli_phase_1/TUI_STRESS_TESTS.md` and `CLI_PHASE1_QUICK_START.md`.

3. **Documentation + Manifest Sync**
   - [x] Refresh `docs/cli_phase_1/TUI_TTY_HARNESS.md` and `TEST_TOOLING_PLAN_CHECKLIST.md` with the new CI workflow diagrams, artifact tables, and sample failure logs.
   - [x] Add a new "CI Contract" appendix to `docs/cli_phase_1/TUI_STRESS_TESTS.md` that enumerates every file emitted per case and links to `artifacts/stress_ci_contract/*` examples.
   - [x] Update `docs/cli_phase_1/TUI_MOCK_BACKEND_PLAYBOOK.md` to remind contributors to start the mock bridge before running the hardened CI gate, highlighting the new failure modes when the backend is absent.

4. **Mock SSE Chaos Hooks Prep**
   - [x] Draft the spec for deterministic SSE/token mock scripts (`docs/cli_phase_1/MOCK_SSE_SPEC.md`) that describes payload format, timing knobs, and integration points.
   - [x] Add CLI flags placeholders (`--mock-sse-script`, `--mock-sse-delay-ms`, etc.) to `scripts/run_stress_bundles.ts` so future work can plug the deterministic mock without further refactors.
   - [x] Record the open questions (e.g., how to encode JSON vs. text streams) inside the spec to unblock planner/engine collaboration later.

5. **Retrofit Layout-Ordering Script**
   - [x] Use the new grid assertions to capture the exact failure from the `layout_ordering` script; store the failing bundle under `artifacts/stress/layout_ordering_failure/`.
   - [x] Annotate the failure in `docs/cli_phase_1/TUI_STRESS_TESTS.md` (Known Issues section) and open a tracker item for the UI fix, so the CI gate can temporarily skip/xfail this specific case with justification.

Completion of this slice should unblock the remaining near-term items and make the CI gate trustworthy before moving onto the deeper mocking + fuzzing milestones.

## Upcoming Execution Slice (Next ~10–15% Scope)

These are the next tightly scoped deliverables. Each must land with updated docs, artifacts, and CI wiring before we move to the remaining mid-term workstreams.

1. **Deterministic Mock SSE Playback (WS4 / WS5 bridge)**
   - [x] Implement the JSON-driven SSE player described in `docs/cli_phase_1/MOCK_SSE_SPEC.md` (`tools/mock/replaySse.ts`). It should read the scripted payload, emit `/events` chunks with optional delays/jitter, and honor loop/stop controls.
   - [x] Teach `scripts/run_cli_bridge_mock.sh` + `scripts/run_stress_bundles.ts` to auto-launch the mock SSE player when a scenario declares `mockSseScript`, wiring through host/port/chaos flags.
   - [x] Add regression fixtures: extend `scripts/mock_hello_script.json` (and a second multi-event scenario) so `npm run stress:bundle -- --case mock_hello` exercises the deterministic path. Bundle outputs (grid/timeline/ttydoc) must live under `artifacts/stress_mock_sse/` for reference.

2. **Chaos Metadata Surfacing**
   - [ ] Propagate bridge-level chaos knobs (latency/jitter/drop) from `scripts/run_cli_bridge_mock.sh` into `timeline_summary.json` (`chaos` block) and `ttydoc.txt`.
   - [ ] Update `tools/assertions/layoutChecks.ts` to copy chaos metadata into `case_info.json`, and teach `stress:ci` to print it alongside guard metrics so reviewers know which knobs were active.
   - [ ] Document the chaos reporting workflow in `docs/cli_phase_1/TUI_STRESS_TESTS.md` (new “Chaos Metadata” subsection) with screenshots of `ttydoc` excerpts.

3. **SIGWINCH Storm Enhancements**
   - [ ] Extend `scripts/resize_storm.json` with randomized / scripted SIGWINCH events via the PTY harness (`--winch-script`) so we can stress-test the new transcript virtualization.
   - [ ] Record per-run resize stats (count, min/max cols/rows, burst duration) in `timeline_summary.json` and flag anomalies when the CLI misses header/composer invariants during storms.
   - [ ] Refresh the corresponding output snapshots and update the Known Issues table if any regressions remain.

4. **Clipboard/Image Assertion Polish**
   - [ ] Expand `tools/assertions/clipboardChecks.ts` to compare `[Pasted Content …]` chips against the stored clipboard manifests (length, MIME, SHA-256) and fail with human-friendly diffs.
   - [ ] Hook the new assertions into `stress:ci` and document the failure mode + remediation steps in `docs/cli_phase_1/TUI_TTY_HARNESS.md`.
   - [ ] Capture before/after runs for a text paste and an image paste (`scripts/ctrl_v_paste.json`, `scripts/attachment_submit.json`) under `artifacts/stress_clipboard/verification_*` for future reference.

5. **Fuzz Harness Reporting**
   - [ ] Summarize `scripts/run_key_fuzz.ts` output within each bundle’s `manifest.json` (iterations run, failures, seed) and surface the same stats in `ttydoc.txt`.
   - [ ] Make `npm run stress:ci` fail if any fuzz iteration crashes, with clear pointers to the `failure-XXX/` folder, and document the workflow in `docs/cli_phase_1/TUI_TESTING_TOOLKIT_PLAN.md`.

Completing this slice will cover the next 10–15 % of the tooling roadmap and unblock the remaining mid-term milestones (full mock provider suite, nightly chaos jobs, and “one-file story” polish).

## Next Detailed Slice (Following 10–15% Scope)

These tasks extend the same roadmap deeper so we can keep moving without pausing. Treat this as the next chunk immediately after the “Upcoming Execution Slice” is complete.

1. **Timeline / TTYDoc Chaos Integration**
   - [ ] Extend `tools/timeline/buildTimeline.ts` so `resizeStats` (from `pty_metadata.json`) and chaos metadata are normalized + versioned; add schema validation to guard against missing keys.
   - [ ] Update `tools/reports/ttydoc.ts` to render three new sections: “Chaos Metadata” (already stubbed), “Resize Stats”, and “Guardrail Metrics Summary” (if `guardrail_metrics.summary.json` exists in the case dir). Provide clear “(none)” fallbacks.
   - [ ] Wire these sections into `scripts/run_stress_bundles.ts`: ensure the case manifest references the guardrail summary path per case, and teach `stress:ci` to fail if summaries are missing when guard logs were provided.
   - [ ] Capture representative ttydoc excerpts (mock SSE + resize storm) under `artifacts/stress_docs/chaos_ttydoc_examples/` and link them from the documentation.

2. **Timeline Budgeting Enhancements**
   - [ ] Update `tools/timeline/checkBudgets.mjs` to track `resizeStats.burstMs` and `resizeStats.count`; add env-configurable budgets (`RESIZE_BURST_BUDGET_MS`, `RESIZE_EVENT_BUDGET`) enforced in `stress:ci`.
   - [ ] Emit structured warnings (JSON lines) whenever budgets are exceeded so CI logs can be machine-parsed later. Store them under `timeline_budget_warnings.jsonl` in each batch.
   - [ ] Document the new knobs + warning files in `docs/cli_phase_1/TUI_STRESS_TESTS.md` and `TUI_TESTING_TOOLKIT_PLAN.md`.

3. **Clipboard Artifact Diffing**
   - [ ] Create `tools/reports/clipboardDiffReport.ts` that reads `clipboard_diffs/*.json` and emits a human-readable summary (length/mime/hash changes). Generate one per batch (`clipboard_diff_report.txt`) during `stress:bundle`.
   - [ ] Teach `stress:ci` to print the diff report (or “no diffs”) and fail when `STRESS_CI_CLIPBOARD_STRICT=1` and the diff isn’t empty. Update docs with a troubleshooting guide referencing `clipboard_diff_report.txt`.
   - [ ] Add regression fixtures: capture “clean” diff reports for `ctrl_v_paste` and `attachment_submit` and store them under `artifacts/stress_clipboard/reference_reports/`.

4. **Attachment Upload Smoke Tests**
   - [ ] Automate the attachment PTY script against the running FastAPI bridge (using the tmux-controlled mock). After each run, verify `/attachments` responded with IDs by parsing `cli.log` and failing fast if no IDs are logged.
   - [ ] Emit a `attachments_summary.json` per case (mapping filenames → attachment IDs) and reference it from `case_info.json` for debugging.
   - [ ] Update `docs/cli_phase_1/TUI_MOCK_BACKEND_PLAYBOOK.md` to describe how the FastAPI bridge now exposes `/attachments` and how the CLI surfaces the resulting IDs in transcripts.

5. **Key-Fuzz Dashboard Hooks**
   - [ ] Promote `artifacts/key_fuzz/summary.json` into the batch manifest (`manifest.json.keyFuzz.summary`). Add a small MDX fragment under `artifacts/stress/<ts>/reports/key_fuzz_report.md` with iteration counts and failure instructions.
   - [ ] Integrate a “key-fuzz status” block into `ttydoc.txt` for the root of the batch (new top-level `reports/ttydoc_batch.txt` summarizing all cases + fuzz results).
   - [ ] Document how to rerun failed fuzz iterations (using `scripts/run_key_fuzz.ts --replay <failure-dir>`) inside `docs/cli_phase_1/TUI_TESTING_TOOLKIT_PLAN.md`.

Finishing this additional slice will push coverage to roughly 65–70 % of the total tooling roadmap, setting us up for the later automation/CI initiatives (nightly chaos, compression, “one-file story” polish).

## Mid Term (Automation & Mocking)

### Mock Provider & Chaos Tools
- [ ] Implement mock SSE/token scripts for deterministic streaming when providers are unreachable.
- [ ] Add latency/jitter knobs to the mock FastAPI bridge and annotate timelines with injected values.
- [ ] Expand resize/paste stress scripts with randomized SIGWINCH storms and capture stats.

### Clipboard/Image Validation
- [x] Add assertions that pasted chips render correctly (text vs. large vs. image) with undo/redo coverage. (See `tools/assertions/clipboardChecks.ts`, new `ctrl_v_paste_large` case, and attachment checks.)
- [x] Include image attachment manifests (size, average color) and compare across runs. (Bundles now copy manifests to `clipboard_manifests/` and record diffs under `clipboard_diffs/`.)

### Key-Sequence Fuzzing
- [x] Create property-based generator for editor sequences (Ctrl/Alt navigation, deletions) and integrate into PTY harness. (`scripts/run_key_fuzz.ts` now executes via `npm run devtools:key-fuzz`, and `stress:bundle`/`stress:ci` invoke it automatically when `--key-fuzz-iterations > 0`.)
- [x] Record invariants (cursor bounds, absence of crashes) and feed results into `stress:bundle` artifacts. (Iteration stats/aggregate metrics are persisted in `key_fuzz/run.json` + `summary.json`, and surfaced in `manifest.json`.)

## Long Term (Advanced Insights)

### Recording Compression & Flamegraphs
- [ ] Implement delta compression for grid/PTY frames (line-level checksums + NDJSON deltas).
- [ ] Generate ASCII flamegraph timelines for each run.

### “One-file Story” Reports
- [ ] Produce `ttydoc.txt` summarizing timeline, anomalies, guard stats, and key screenshots per run.
- [ ] Link reports from `manifest.json` and CI artifacts.

### Chaos/Fuzz Suites at Scale
- [ ] Nightly job that runs extended chaos suite (mock SSE + latency + fuzz). Store bundles for comparison.
- [ ] Alerting/notification when anomaly counts or metrics drift beyond budgets.

---
**Usage:** Update this file after each task. Reference it in status updates to keep everyone aligned on progress.
