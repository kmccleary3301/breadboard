# E4 Recalibration Status (V1)

Date: 2026-03-03

## Scope

Operational status for the E4 target-freeze recalibration campaign.

Primary plan:

- `docs/conformance/E4_RECALIBRATION_PLAN_V1.md`

## Reference sync + drift status

- `codex` reference clone updated to `56cc2c71f40c5349b9e48351731c589127faeea6`.
- `claude-code` reference clone updated to `38281cfd46336ec4c21dfb8f29a649515cc09dad`.
- `opencode` reference clone fetched; updater now reads `origin/HEAD` to avoid dirty-worktree pin skew.

Artifacts:

- `artifacts/conformance/e4_target_drift_audit_report.json`
- `artifacts/conformance/e4_target_refresh_plan.after_refsnapshot_fix.json`
- `artifacts/conformance/e4_target_freshness_45d_report.json`
- `artifacts/conformance/e4_target_freshness_90d_report.json`

Freshness milestone:

- `config/e4_target_freeze_manifest.yaml` now includes fresh OpenCode Batch A
  replay evidence links under:
  - `docs/conformance/e4_recalibration_evidence/e4_batchA_serial_20260302_204843/`
- `python scripts/check_e4_target_freeze_manifest.py --strict-evidence --max-evidence-age-days 45 --json`
  now returns `ok: true`.

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

1. Refresh evidence captures/session-dumps for all drifted lanes at current upstream targets.
2. Update `config/e4_target_freeze_manifest.yaml` via `scripts/update_e4_target_freeze_manifest.py --write`
   only when evidence has been refreshed for those lanes.
3. Re-run strict/freshness checks (`--strict-evidence --max-evidence-age-days 45`) and lane parity.
4. Close drift (`drift_count == 0`) and record per-lane bump notes.
