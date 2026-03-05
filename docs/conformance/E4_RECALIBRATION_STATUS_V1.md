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

## Post-restore strict replay probe baseline (2026-03-04)

Additional post-restore evidence was generated to pin strict replay probe
behavior for Codex/Claude/OpenCode lanes.

Quick rerun command:

```bash
make e4-postrestore-strict-probe
```

Codex strict replay bundle:

- run id: `codex_capture_refresh_20260304_postfix`
- artifact: `artifacts/parity_runs/codex_capture_refresh_20260304_postfix/parity_summary.json`
- result: 3/3 passed (`codex_cli_mvi_patch_v2_replay`, `codex_cli_subagent_sync_replay`, `codex_cli_subagent_async_replay`)

Claude + OpenCode strict replay bundle:

- run id: `claude_opencode_replay_probe_strict_20260304_v2`
- artifact: `artifacts/parity_runs/claude_opencode_replay_probe_strict_20260304_v2/parity_summary.json`
- result: 4/4 passed
  - `opencode_patch_todo_sentinel_replay`
  - `opencode_glob_grep_sentinel_replay`
  - `opencode_toolcall_repair_sentinel_replay`
  - `claude_e4_refresh_ping_replay_20260304`

Notes:

- Legacy Claude replay fixtures referenced by older parity scenarios are still
  absent in the restored tree; the new
  `claude_e4_refresh_ping_replay_20260304` lane is the canonical low-spend
  strict replay probe baseline until those fixtures are regenerated.
- `opencode_patch_todo_sentinel_replay` now points to a restored deterministic
  golden workspace snapshot path:
  - `misc/opencode_runs/golden_live/opencode_patch_todo_sentinel_20260304`

## Claude legacy replay lane restore probe (2026-03-04)

Legacy Claude phase8/protofs replay fixture lanes were restored and revalidated
in strict mode.

Quick rerun command:

```bash
make e4-claude-legacy-strict-probe
```

Latest strict run:

- run id: `claude_legacy_fixture_restore_probe_20260304_strict`
- artifact: `artifacts/parity_runs/claude_legacy_fixture_restore_probe_20260304_strict/parity_summary.json`
- result: 11/11 passed, 0 warns

Scenarios covered:

- `claude45_protofs_replay`
- `claude_code_task_subagent_sync_replay`
- `claude_code_phase8_async_subagents_v1_replay`
- `claude_code_phase8_subagent_nested_spawn_v1_replay`
- `claude_code_phase8_async_subagent_wakeup_ordering_v1_replay`
- `claude_code_phase8_async_wakeup_eventlog_replay`
- `claude_code_phase8_subagent_write_denial_v1_replay`
- `claude_code_phase8_subagent_permission_propagation_v1_replay`
- `claude_code_phase8_subagent_allowlist_denial_v1_replay`
- `claude_code_phase8_async_subagent_resume_taskoutput_v1_replay`
- `claude_code_phase8_subagent_resume_success_v1_replay`

## Capture refresh tranche (2026-03-05)

Refreshed target-harness captures were regenerated against current local clones,
and versioned snapshot configs were added without deleting prior snapshots.

Capture bundle run ids:

- Codex refresh bundle:
  - `e4_refresh_20260305_fix3_codex_ping`
  - `e4_refresh_20260305_fix3_codex_sub_sync`
  - `e4_refresh_20260305_fix3_codex_sub_async`
- Codex MVI refresh lane:
  - `e4_refresh_20260305_fix3_codex_mvi_patch_v2`
- Claude refresh ping:
  - `e4_refresh_20260305_fix3_claude_claude_ping`
- OpenCode refresh ping:
  - `e4_refresh_20260305_fix3_opencode_opencode_ping`

Replay probe reruns after refresh:

- `e4_postrestore_strict_probe_20260305_030615` (7/7 passed)
- `e4_claude_legacy_strict_probe_20260305_030706` (11/11 passed)

Snapshot/versioning:

- Manifest pins refreshed to latest local Codex/OpenCode clone commits.
- New snapshot tag created:
  - `codex0_1070_claude2_0_72_opencode1_2_17_20260305`
- New versioned E4 config entries added for all codex/claude/opencode E4
  base rows under `agent_configs/*__codex0_1070_claude2_0_72_opencode1_2_17_20260305.yaml`.

Tooling hardening in this tranche:

- `scripts/capture_codex_golden.sh`
  - fixed Codex 0.107.x reasoning override key:
    - from `reasoning.effort` to `model_reasoning_effort`
  - added `--reasoning-effort` flag (default `high`)
  - added optional `--isolate-home` mode (off by default) for deterministic
    runs when local auth context is not required.
- `scripts/capture_opencode_golden.sh`
  - dependency bootstrap now also repairs missing
    `node_modules/@aws-sdk/credential-providers` after upstream pulls.

## Follow-up refresh (Claude 2.1.63, 2026-03-05)

Additional follow-up aligned the Claude lane to current local CLI/runtime and
re-synced all harness pin commits to current local clone heads.

Capture evidence:

- Claude ping capture (`version=2.1.63`):
  - run id: `e4_refresh_20260305_fix4_claude_ping`
  - path:
    - `misc/claude_code_runs/goldens/2.1.63/claude_e4_refresh_ping_v1/runs/e4_refresh_20260305_fix4_claude_ping`

New snapshot tag generated:

- `codex0_1070_claude2_1_63_opencode1_2_17_20260305`

Added versioned rows:

- `claude_code_haiku45_e4_replay__codex0_1070_claude2_1_63_opencode1_2_17_20260305`
- `codex_cli_gpt51mini_e4_live__codex0_1070_claude2_1_63_opencode1_2_17_20260305`
- `codex_cli_gpt5_e4_live__codex0_1070_claude2_1_63_opencode1_2_17_20260305`
- `opencode_e4_glob_grep_sentinel_replay__codex0_1070_claude2_1_63_opencode1_2_17_20260305`
- `opencode_e4_mvi_replay__codex0_1070_claude2_1_63_opencode1_2_17_20260305`
- `opencode_e4_oc_protofs_gpt5nano_replay__codex0_1070_claude2_1_63_opencode1_2_17_20260305`
- `opencode_e4_patch_todo_sentinel_replay__codex0_1070_claude2_1_63_opencode1_2_17_20260305`
- `opencode_e4_toolcall_repair_sentinel_replay__codex0_1070_claude2_1_63_opencode1_2_17_20260305`
- `opencode_e4_webfetch_sentinel_replay__codex0_1070_claude2_1_63_opencode1_2_17_20260305`

Strict replay probe reruns:

- `e4_postrestore_strict_probe_20260305_054103`
  - artifact: `artifacts/parity_runs/e4_postrestore_strict_probe_20260305_054103/parity_summary.json`
  - result: `status_counts.passed = 7`, `failed = []`, `warned = []`
- `e4_claude_legacy_strict_probe_20260305_054223`
  - artifact: `artifacts/parity_runs/e4_claude_legacy_strict_probe_20260305_054223/parity_summary.json`
  - result: `status_counts.passed = 11`, `failed = []`, `warned = []`

Manifest/drift verification:

- `python scripts/check_e4_target_freeze_manifest.py --strict-evidence --json` -> `ok: true`
- `python scripts/audit_e4_target_drift.py --json-out artifacts/conformance/e4_target_drift_audit_report.20260305_fix4_final.json`
  - `drift_count: 0` (all 44 E4 rows aligned at run time)
