# E4 Recalibration Plan (V1)

Date: 2026-03-03  
Owner: Engine parity / conformance lane

## Objective

Recalibrate every E4 config lane to its current upstream harness snapshot in a controlled,
evidence-first order, without silently changing target pins.

## Policy decision (CI scope)

- Heavy recalibration cadence is intentionally **not** run on schedule in GitHub Actions.
- Full recalibration remains an operator-driven/manual action (`workflow_dispatch` or local runbook).
- CI keeps lightweight drift visibility only (live/snapshot drift reports), not full provider replay/capture loops.

This plan is driven by:

- `artifacts/conformance/e4_target_drift_audit_report.json`
- `artifacts/conformance/e4_target_refresh_plan.after_refsnapshot_fix.json`
- `artifacts/conformance/e4_target_freshness_45d_report.json`
- `artifacts/conformance/e4_target_freshness_90d_report.json`

## Current state snapshot

- Drift vs upstream: **8 / 8 lanes drifted**
- Refresh proposal from local references: **8 / 8 lanes would change**
- Freshness at 45d: **5 stale lanes** (all OpenCode replay/sentinel lanes)
- Freshness at 90d: **pass**

## Lanes and priorities

### P0 (shared upstream, most stale): OpenCode sentinel/replay family

1. `opencode_e4_mvi_replay`
2. `opencode_e4_glob_grep_sentinel_replay`
3. `opencode_e4_patch_todo_sentinel_replay`
4. `opencode_e4_toolcall_repair_sentinel_replay`
5. `opencode_e4_webfetch_sentinel_replay`
6. `opencode_e4_oc_protofs_gpt5nano_replay`

Target upstream commit (current): `98c75be7e1ab72c48985be033862d96209d4069b`

### P1: Codex CLI live lane

7. `codex_cli_gpt51mini_e4_live`

Target upstream commit (current): `56cc2c71f40c5349b9e48351731c589127faeea6`

### P1: Claude Code replay lane

8. `claude_code_haiku45_e4_replay`

Target upstream commit (current): `38281cfd46336ec4c21dfb8f29a649515cc09dad`

## Hard guardrails

1. Do not change `config/e4_target_freeze_manifest.yaml` pin fields without refreshed calibration evidence.
2. For each lane, evidence refresh and parity verification happen in the same PR/commit tranche.
3. Only bump lanes that pass their lane-specific replay/live checks.
4. If a lane fails, pin remains unchanged and failure is recorded.

## Execution protocol (per lane)

1. Confirm reference status:
   - `make e4-target-refresh-plan`
   - `make e4-target-drift-audit`
2. Refresh lane evidence:
   - replay lanes: regenerate session dumps / replay artifacts for that lane.
   - live lanes: re-run tmux scenario captures.
3. Validate lane behavior:
   - run lane replay/live checks and parity checks.
4. Update manifest row:
   - upstream commit/date
   - release label (if changed)
   - calibration anchor (`run_id` and `evidence_paths`)
5. Re-run global checks:
   - `python scripts/check_e4_target_freeze_manifest.py --strict-evidence --json`
   - `python scripts/check_e4_target_freeze_manifest.py --strict-evidence --max-evidence-age-days 45 --json`
6. Land with artifacts and short lane summary.

## Suggested batch order

### Batch A: OpenCode family (single upstream)

Goal: clear stale evidence + reduce 6 lanes of drift quickly.

Checklist:

- [ ] Update OpenCode captures/dumps for all 6 OpenCode lanes.
- [ ] Re-run OpenCode replay parity checks lane-by-lane.
- [ ] Patch all 6 manifest rows in one controlled commit.
- [ ] Verify 45d freshness for OpenCode rows now passes.

### Batch B: Codex live lane

Checklist:

- [ ] Run current codex tmux scenario capture for E4 calibration.
- [ ] Validate codex lane parity.
- [ ] Update codex manifest row + evidence paths.

### Batch C: Claude replay lane

Checklist:

- [ ] Refresh claude replay/snapshot evidence.
- [ ] Validate claude lane parity.
- [ ] Update claude manifest row + evidence paths.

## Completion criteria

All must be true:

1. `e4_target_drift_audit_report.json` has `drift_count == 0`.
2. `check_e4_target_freeze_manifest.py --strict-evidence --max-evidence-age-days 45 --json` returns `ok: true`.
3. Each lane has new evidence artifacts linked in manifest.
4. E4 lane replay/live checks pass for all 8 lanes.
5. No scheduled heavy recalibration workflow is introduced; manual-only policy remains enforced.

## Rollback strategy

If any lane regresses:

1. Revert only that lane’s config and manifest row to previous pin/evidence.
2. Keep other lane bumps intact.
3. Record failure signature and open follow-up issue tied to lane key.

## Operator commands

```bash
# Snapshot current drift/freshness
make e4-target-refresh-plan || true
make e4-target-drift-audit || true
python scripts/check_e4_target_freeze_manifest.py --strict-evidence --max-evidence-age-days 45 --json

# After lane evidence refresh, apply pin updates
python scripts/update_e4_target_freeze_manifest.py --write

# Validate manifest hard
python scripts/check_e4_target_freeze_manifest.py --strict-evidence --json
python scripts/check_e4_target_freeze_manifest.py --strict-evidence --max-evidence-age-days 45 --json
```

## Notes

- `scripts/update_e4_target_freeze_manifest.py` now prefers `origin/HEAD` when present,
  so planning remains stable even if a reference clone worktree is dirty.
- OpenCode reference clone currently has a dirty worktree, but fetched `origin/HEAD`
  is used for pin planning and does not block recalibration.
