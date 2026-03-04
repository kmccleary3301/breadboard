# E4 Recalibration Status (V1)

Date: 2026-03-04

## Scope

Operational status for the E4 target-freeze recalibration campaign.

Primary plan:

- `docs/conformance/E4_RECALIBRATION_PLAN_V1.md`
- `docs/conformance/E4_CODEX_LIVE_REFRESH_RUNBOOK.md`

## Reference sync + drift status

- Snapshot window files:
  - `artifacts/conformance/e4_refsnapshot_20260304_0300.json` (local run artifact)
  - `docs/conformance/e4_recalibration_evidence/e4_refsnapshot_20260304_0300.json` (tracked copy)
- Snapshot commits:
  - codex: `e6773f856c97ce766b7f507a99e5447a1e2a306c`
  - claude-code: `a8335230bc62baae60c8eb18ae77ab32123454bd`
  - opencode: `3ebebe0a96de5ec4757de12a21cb9f933e0fbba0`

Artifacts:

- `artifacts/conformance/e4_target_drift_live_head_report.json`
- `artifacts/conformance/e4_target_drift_snapshot_report.json`
- `artifacts/conformance/e4_target_refresh_plan.after_refsnapshot_fix.json`
- `artifacts/conformance/e4_target_freshness_45d_report.json`
- `artifacts/conformance/e4_target_freshness_90d_report.json`

Freshness/evidence milestone:

- Fresh live capture anchors were refreshed for:
  - `codex_cli_gpt51mini_e4_live` -> run `20260304-040559`
  - `claude_code_haiku45_e4_replay` -> run `20260304-041108`
- OpenCode replay family refreshed:
  - `e4_batchA_serial_20260304_3`
- `config/e4_target_freeze_manifest.yaml` now points codex/claude rows to those
  run IDs and current snapshot commits.
- Repo-local evidence mirrors were restored under:
  - `docs_tmp/tmux_captures/scenarios/nightly_provider/*`
  - `misc/opencode_runs/*`
  - `misc/opencode_tests/*`
- `python scripts/check_e4_target_freeze_manifest.py --strict-evidence --json`
  returns `ok: true`.
- `python scripts/check_e4_target_freeze_manifest.py --strict-evidence --max-evidence-age-days 45 --json`
  returns `ok: true`.

Drift milestone (current, snapshot-based):

- Drift closed: `drift_count == 0` (when compared to the frozen snapshot file)
- Aligned lane count: `aligned_count == 8`
- Open drift lanes: none.
- Current drift reports:
  - `artifacts/conformance/e4_target_drift_live_head_report.json`
  - `artifacts/conformance/e4_target_drift_snapshot_report.json`
- Current refresh proposal:
  - `artifacts/conformance/e4_target_refresh_plan.json`

CI automation (manual + nightly):

- Manual recalibration wrapper workflow:
  - `.github/workflows/e4-recalibration-snapshot.yml`
  - runs `scripts/run_e4_snapshot_recalibration.py` end-to-end and uploads
    codex/claude capture evidence + OpenCode Batch A evidence + drift reports.
  - policy: `workflow_dispatch` only (no schedule).
- Nightly drift workflow now publishes both views:
  - live head drift (`e4_target_drift_live_head_report.json`)
  - snapshot drift (`e4_target_drift_snapshot_report.json`, when snapshot exists)
  - workflow: `.github/workflows/e4-target-drift-audit-nightly.yml`

Policy decision (scoped item 5):

- Heavy CI/GitHub Actions recalibration cadence is **struck down**.
- Keep heavy recalibration manual-only to avoid CI cost/flakes/provider-dependency coupling.
- Keep nightly drift visibility (lightweight) as the only scheduled automation in this area.

## Batch A baseline (OpenCode replay family)

Run IDs:

- `e4_batchA_serial_20260302_204843` (baseline)
- `e4_batchA_serial_20260304_3` (latest snapshot refresh)

Artifacts:

- `artifacts/parity_runs/e4_batchA_serial_20260302_204843/serial_run_status.json`
- `artifacts/parity_runs/e4_batchA_serial_20260304_3/parity_summary.json`

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

1. None for this recalibration pass; strict evidence checks and snapshot-based drift audit are green:
   - `python scripts/check_e4_target_freeze_manifest.py --strict-evidence --json` -> `ok: true`
   - `python scripts/check_e4_target_freeze_manifest.py --strict-evidence --max-evidence-age-days 45 --json` -> `ok: true`
   - `python scripts/audit_e4_target_drift.py --snapshot-json artifacts/conformance/e4_refsnapshot_20260304_0300.json ...` -> `drift_count: 0`
2. Hardening landed in `scripts/start_tmux_phase4_replay_target.sh`:
   - defaults replay targets to safe `/tmp/breadboard_replay_<session>_ws` workspace
   - auto-falls back to safe workspace when repo-root is requested
   - prefers `--use-dist` by default and auto-builds `tui_skeleton/dist/main.js` if missing
   - supports `--use-dev` override for explicit tsx/dev runtime.
