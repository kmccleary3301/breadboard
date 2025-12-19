# UX Overhaul Completion Report (Live Provider Verified)

## What was finished
- **Live-slot + tool log polish**: Tool events are now structured (`ToolLogEntry`) with status glyphs, inline diff previews, and collapse summaries in both Ink and scripted renderers (`kylecode_cli_skeleton/src/commands/repl/controller.ts`, `src/repl/components/ReplView.tsx`, `src/commands/repl/renderText.ts`). Guardrail banner actions log expand/collapse/dismiss events for telemetry parity.
- **Transcript virtualization clarity**: Compact mode shows explicit hints (`window N rows, showing last M messages (K hidden)`) so operators know exactly how much history is trimmed. Log-window fixtures capture compact vs. full modes (`scripts/log_window_compact.txt`, `scripts/log_window_full.txt` generated via `npm run stress:logwindow`).
- **Attachment + /retry flow**: Submissions cache attachment IDs; `/retry [n]` replays any prior prompt with its cached IDs. The playbook documents an attachment retry scenario and how to verify uploads once via `/attachments`, then reuse IDs on `/retry` (`docs/cli_phase_1/TUI_MOCK_BACKEND_PLAYBOOK.md`).
- **Replay/metrics automation**: One-command helpers now exist for snapshots (`npm run stress:snapshots`, `npm run stress:logwindow`) and guardrail metrics (`npm run guardrails:metrics` → `artifacts/cli_guardrail_metrics.{jsonl,summary.json}`). Guardrail docs explain how to read the metrics outputs (`docs/phase_5/CLI_GUARDRAIL_STATUS.md`, `docs/phase_5/GUARDRAILS_OVERVIEW.md`).
- **Live-provider sample captured**: Running with a real provider config succeeded:  
  ```
  node dist/main.js repl --remote-stream \
    --config ../agent_configs/opencode_openai_codexmini_c_fs_cli_shared.yaml \
    --script scripts/mock_hello_script.json \
    --script-output scripts/live_provider_sample.txt \
    --script-final-only
  ```
  Session `b7843af5-2ab1-4e38-8501-8d45e9fc9030` streamed normally and wrote `scripts/live_provider_sample.txt` (no timeouts, guardrail-only stalls, or attachment failures).

## Artifacts & fixtures
- **Stress suites** (shared-engine defaults, base URL `http://127.0.0.1:9099`):
  - `scripts/mock_hello_output_cli_shared_latest.txt`
  - `scripts/modal_overlay_stress_cli_shared_latest.txt`
  - `scripts/resize_storm_cli_shared_latest.txt`
  - `scripts/paste_flood_output_cli_shared_latest.txt`
  - `scripts/token_flood_output_cli_shared_latest.txt`
  - PTY clips: `scripts/ctrl_v_paste_output_cli_shared.txt`, `scripts/attachment_submit_output_cli_shared.txt`
  - Log windows: `scripts/log_window_compact.txt`, `scripts/log_window_full.txt`
- **Guardrail fixtures**: `misc/cli_guardrail_tests/*.guardrails.json` remain synced to the 20251114 log bundle; metrics commands default to those directories.
- **Metrics outputs**: `artifacts/cli_guardrail_metrics.jsonl` + `artifacts/cli_guardrail_metrics.summary.json` (generated via `npm run guardrails:metrics`).
- **Live-provider transcript**: `scripts/live_provider_sample.txt` from the codexmini shared config.

## Commands to regenerate
- Build + tests: `npm run build && npm test`
- Full snapshot battery: `npm run stress:snapshots`
- Log-window fixtures only: `npm run stress:logwindow`
- Guardrail metrics (curated logs): `npm run guardrails:metrics`
- Live-provider smoke (uses shared config + default API URL):  
  `node dist/main.js repl --remote-stream --config ../agent_configs/opencode_openai_codexmini_c_fs_cli_shared.yaml --script scripts/mock_hello_script.json --script-output scripts/live_provider_sample.txt --script-final-only`

## Outstanding risks / notes
- CI wiring is not yet hooked; the helpers above are ready to drop into a workflow once we decide to publish snapshots/metrics on PRs.
- Live-provider sample used the shared codexmini config; if provider quotas or keys change, re-run the command above to refresh `scripts/live_provider_sample.txt`.
- Guardrail event coverage still depends on the curated 20251114 logs; capturing a new real-provider log bundle would improve parity confidence but is optional for this UX milestone.
