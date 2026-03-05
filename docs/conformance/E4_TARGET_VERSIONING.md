# E4 Target Versioning

BreadBoard E4 configs are now version-pinned to explicit upstream harness snapshots via:

- `config/e4_target_freeze_manifest.yaml`

## Why this exists

Target harnesses (Codex CLI, Claude Code, OpenCode, and related variants) evolve quickly.  
Without explicit pinning, E4 can drift silently and parity assertions become ambiguous.

This manifest ensures each E4 config maps to:

1. The upstream harness repo + commit snapshot.
2. A release label used in parity reports.
3. A concrete calibration anchor (tmux capture or replay session dump evidence).

## Scope

Current enforced scope is every file matching:

- `agent_configs/*e4*.yaml`

Each such config must have a corresponding manifest entry.

## Validation

Run:

```bash
make e4-target-manifest
```

Equivalent direct check:

```bash
python scripts/check_e4_target_freeze_manifest.py --json
```

Optional strict evidence check:

```bash
python scripts/check_e4_target_freeze_manifest.py --strict-evidence --json
```

Optional freshness check (fails stale calibration anchors):

```bash
python scripts/check_e4_target_freeze_manifest.py --strict-evidence --max-evidence-age-days 45 --json
```

## Refresh helpers

Generate a dry-run update plan from local harness clones in `../other_harness_refs`:

```bash
make e4-target-refresh-plan
```

Equivalent direct command:

```bash
python scripts/update_e4_target_freeze_manifest.py --check --json-out artifacts/conformance/e4_target_refresh_plan.json
```

Apply the refresh in-place:

```bash
python scripts/update_e4_target_freeze_manifest.py --write
```

Create side-by-side versioned E4 config snapshots (preserve old files/rows):

```bash
python scripts/create_versioned_e4_snapshot_configs.py --snapshot-tag <tag>
```

Example:

```bash
python scripts/create_versioned_e4_snapshot_configs.py --snapshot-tag codex_cli_0_105_0_20260304
```

More explicit multi-harness example (recommended):

```bash
python scripts/create_versioned_e4_snapshot_configs.py --snapshot-tag codex0_1050_claude2_0_72_opencode1_2_6_20260304
```

## Nightly drift audit

A nightly workflow (`.github/workflows/e4-target-drift-audit-nightly.yml`) checks
manifest-pinned commits against upstream remote HEADs and uploads:

- `artifacts/e4_target_drift_live_head_report.json`
- `artifacts/e4_target_drift_snapshot_report.json` (when snapshot JSON is available)

Local equivalent:

```bash
make e4-target-drift-audit
```

## Update procedure when target harness changes

1. Pull/update upstream harness reference repositories.
2. Generate refresh plan and inspect drift:
   - `make e4-target-refresh-plan`
   - `make e4-target-drift-audit`
3. Capture new evidence:
   - tmux provider scenario captures for interactive parity lanes.
   - replay session dumps for OpenCode/other replay lanes.
   - use manual recalibration (`.github/workflows/e4-recalibration-snapshot.yml`, `workflow_dispatch`) for heavy refresh.
4. Add/adjust manifest rows with:
   - upstream commit + date,
   - release label,
   - updated evidence paths.
5. Update E4 config behavior only as required for parity.
6. Re-run E4 checks and conformance runs.
7. Record the bump in release notes / parity docs.

## Post-Restore Strict Probe Baseline (2026-03-04)

After repository restore and fixture reindexing, we established an explicit
strict replay probe baseline for current Codex/Claude/OpenCode parity surfaces.

Canonical strict probe run:

```bash
make e4-postrestore-strict-probe
```

Equivalent explicit command:

```bash
python scripts/run_parity_replays.py --strict \
  --scenario claude_e4_refresh_ping_replay_20260304 \
  --scenario opencode_patch_todo_sentinel_replay \
  --scenario opencode_glob_grep_sentinel_replay \
  --scenario opencode_toolcall_repair_sentinel_replay \
  --scenario codex_cli_mvi_patch_v2_replay \
  --scenario codex_cli_subagent_sync_replay \
  --scenario codex_cli_subagent_async_replay \
  --parity-run-id e4_postrestore_strict_probe_<utc_timestamp>
```

Baseline semantics:

- Codex modern lanes target `bitwise_trace` (`0.105.0` event schema).
- OpenCode patch/todo sentinel targets `normalized_trace` (deterministic replay
  with restored golden workspace snapshot).
- OpenCode glob/grep + toolcall-repair sentinels target `bitwise_trace`.
- Claude refresh ping replay lane targets `normalized_trace` for low-spend
  deterministic post-restore probes when older protofs/phase8 replay fixtures
  are unavailable in the restored tree.

Primary evidence references from this tranche:

- `artifacts/parity_runs/codex_capture_refresh_20260304_postfix/parity_summary.json`
- `artifacts/parity_runs/claude_opencode_replay_probe_strict_20260304_v2/parity_summary.json`

This baseline is the current "go/no-go" strict replay probe set for post-restore
E4 confidence and should be rerun whenever target harness version snapshots are bumped.

## Required E4 config header convention

Each E4 config should include:

```yaml
# e4_target_key: <config_stem>
# e4_target_manifest: config/e4_target_freeze_manifest.yaml
```

This is non-functional metadata for maintainers and reviewers.

## CI boundary

- Scheduled CI should remain lightweight (drift visibility only).
- Do not add scheduled heavy recalibration/provider replay capture loops to GitHub Actions.
