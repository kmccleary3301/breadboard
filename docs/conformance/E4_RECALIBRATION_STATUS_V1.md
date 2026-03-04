# E4 Recalibration Status (V1)

Date: 2026-03-04

## Scope

Operational status for the E4 target-freeze recalibration campaign.

Primary plan:

- `docs/conformance/E4_RECALIBRATION_PLAN_V1.md`
- `docs/conformance/E4_CODEX_LIVE_REFRESH_RUNBOOK.md`

## Reference sync + drift status

- `codex` reference clone updated to `e951ef43741628a4835dceaf61df297c504b7281`.
- `claude-code` reference clone updated to `38281cfd46336ec4c21dfb8f29a649515cc09dad`.
- `opencode` reference clone fetched; updater now reads `origin/HEAD` to avoid dirty-worktree pin skew.

Artifacts:

- `artifacts/conformance/e4_target_drift_audit_report.json`
- `artifacts/conformance/e4_target_refresh_plan.after_refsnapshot_fix.json`
- `artifacts/conformance/e4_target_freshness_45d_report.json`
- `artifacts/conformance/e4_target_freshness_90d_report.json`

Freshness/evidence milestone:

- Fresh live capture anchors were refreshed for:
  - `codex_cli_gpt51mini_e4_live` -> run `20260304-005206`
  - `claude_code_haiku45_e4_replay` -> run `20260304-000215`
- `config/e4_target_freeze_manifest.yaml` now points codex/claude rows to those
  run IDs and current upstream commits.
- Repo-local evidence mirrors were restored under:
  - `docs_tmp/tmux_captures/scenarios/nightly_provider/*`
  - `misc/opencode_runs/*`
  - `misc/opencode_tests/*`
- `python scripts/check_e4_target_freeze_manifest.py --strict-evidence --json`
  returns `ok: true`.
- `python scripts/check_e4_target_freeze_manifest.py --strict-evidence --max-evidence-age-days 45 --json`
  returns `ok: true`.

Drift milestone (current):

- Drift closed: `drift_count == 0`
- Aligned lane count: `aligned_count == 8`
- Open drift lanes: none.
- Current drift report:
  - `artifacts/conformance/e4_target_drift_audit_report.json`
- Current refresh proposal:
  - `artifacts/conformance/e4_target_refresh_plan.json`

## Batch A baseline (OpenCode replay family)

Run ID:

- `e4_batchA_serial_20260302_204843`

Artifact:

- `artifacts/parity_runs/e4_batchA_serial_20260302_204843/serial_run_status.json`

Result:

- `opencode_mvi_bash_write_replay`: exit `0`
- `opencode_protofs_gpt5nano_toolio_replay`: exit `0`
- `opencode_patch_todo_sentinel_replay`: exit `0`
- `opencode_glob_grep_sentinel_replay`: exit `0`
- `opencode_toolcall_repair_sentinel_replay`: exit `0`
- `opencode_webfetch_sentinel_replay`: exit `0`

## Reliability notes

One replay runner defect was fixed:

- `scripts/run_parity_replays.py`: removed duplicate YAML seeding branch causing
  `cannot access local variable 'yaml'` warning in replay mode.

Memory guard:

- For stable local replay batches on this machine, use:
  - `RAY_memory_usage_threshold=0.99`

This avoids intermittent Ray worker kills under high ambient memory pressure.

## Remaining work (from plan)

1. None for this recalibration pass; strict evidence checks and drift audit are green:
   - `python scripts/check_e4_target_freeze_manifest.py --strict-evidence --json` -> `ok: true`
   - `python scripts/check_e4_target_freeze_manifest.py --strict-evidence --max-evidence-age-days 45 --json` -> `ok: true`
   - `python scripts/audit_e4_target_drift.py ...` -> `drift_count: 0`
2. Hardening landed in `scripts/start_tmux_phase4_replay_target.sh`:
   - defaults replay targets to safe `/tmp/breadboard_replay_<session>_ws` workspace
   - auto-falls back to safe workspace when repo-root is requested
   - prefers `--use-dist` by default and auto-builds `tui_skeleton/dist/main.js` if missing
   - supports `--use-dev` override for explicit tsx/dev runtime.
