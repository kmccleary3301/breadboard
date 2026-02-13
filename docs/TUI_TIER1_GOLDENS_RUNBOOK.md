# TUI Tier-1 Goldens Runbook (U1/U2/U3)

This document defines the Tier-1 deterministic gates and the operator workflows for:
1. report generation,
2. strict gating, and
3. explicit blessing.

## Canary (What Must Be Green To Merge)

Canonical strict canary for PR merges:
1. Workflow: `.github/workflows/ci.yml`
2. Job: `tui` (ubuntu)
3. Required strict steps:
   - `node --import tsx tui_skeleton/scripts/tui_u1_goldens_strict.ts`
   - `node --import tsx tui_skeleton/scripts/tui_u2_goldens_strict.ts`
   - `node --import tsx tui_skeleton/scripts/tui_u3_goldens_strict.ts`

Notes:
1. `tui:goldens:report` is report-only by design (it must not fail CI), and is intended to provide visibility for drift.
2. Broad matrix coverage beyond the canary is handled by Tier-2 nightly/report workflows and by local scripts.

## Tier-1 Scenario IDs and Acceptance Invariants

## U1 (ASCII / structural)

Manifest: `tui_skeleton/ui_baselines/u1/manifests/u1.yaml`

Scenarios:
1. `tool_call_result_collapsed`
2. `streaming_pending_basic`
3. `ascii_only_pending`

Acceptance invariants:
1. ASCII-safe rendering (`ascii_only: true`)
2. Stable structural text with normalization of timestamps/tokens/paths/status
3. Strict compare must pass on blessed `render.txt`

## U2 (Unicode + color structural)

Manifest: `tui_skeleton/ui_baselines/u2/manifests/u2.yaml`

Scenarios:
1. `tool_call_result_collapsed`
2. `streaming_pending_basic`
3. `pending_unicode_color`

Acceptance invariants:
1. Unicode + truecolor render path is exercised (`ascii_only: false`, `color_mode: truecolor`)
2. ANSI output required (`style.require_ansi: true`)
3. Structural compare and strict compare must pass on blessed snapshots

## U3 (Diff/style invariants)

Manifest: `tui_skeleton/ui_baselines/u3/manifests/u3.yaml`

Scenarios:
1. `diff_patch_preview`

Acceptance invariants:
1. ANSI output required
2. Diff add background present (`style.diff_add_bg: true`)
3. Diff delete background present (`style.diff_del_bg: true`)
4. Tool dot marker present (`style.tool_dot: true`)
5. Strict compare must pass on blessed snapshots

## CI Workflows

1. `/.github/workflows/ci.yml` (canonical strict canary)
2. `/.github/workflows/tui-goldens-tier2-nightly.yml` (broad report-only coverage)

Coverage policy:
1. Canonical strict canary runs on `ci.yml` (ubuntu) using the default preset unless explicitly overridden.
2. Tier-2 nightly/report workflow is the matrix coverage surface (preset/mode/width combinations) and is report-only by design.
3. Local `./scripts/run_tui_tier1_local.sh` is the recommended operator entrypoint to rerun the preset/mode matrix and then enforce strict gates.

## Local Commands

## One-command Tier-1 rerun (report matrix + strict gates)

```bash
./scripts/run_tui_tier1_local.sh
```

## One-command Tier-2 rerun (report-only; broader matrix)

```bash
./scripts/run_tui_tier2_local_report.sh
```

## Per-lane report

```bash
cd tui_skeleton
node --import tsx scripts/tui_u1_goldens_report.ts
node --import tsx scripts/tui_u2_goldens_report.ts
node --import tsx scripts/tui_u3_goldens_report.ts
```

## Per-lane strict gate

```bash
cd tui_skeleton
node --import tsx scripts/tui_u1_goldens_strict.ts
node --import tsx scripts/tui_u2_goldens_strict.ts
node --import tsx scripts/tui_u3_goldens_strict.ts
```

## Blessing Workflow (explicit and separate from gates)

Blessing is a manual operator action and must not be embedded into strict gate CI.

1. Generate candidate run:

```bash
cd tui_skeleton
node --import tsx scripts/run_tui_goldens.ts --manifest ui_baselines/u1/manifests/u1.yaml --out ui_baselines/u1/_runs
```

2. Select candidate run directory (`ui_baselines/u1/_runs/run-<timestamp>`).
3. Bless selected candidate:

```bash
cd tui_skeleton
node --import tsx scripts/bless_tui_goldens.ts \
  --manifest ui_baselines/u1/manifests/u1.yaml \
  --source ui_baselines/u1/_runs/run-<timestamp> \
  --blessed-root ui_baselines/u1/scenarios
```

4. Re-run strict compare after blessing and confirm green before commit.

## Notes

1. Tier-1 is deterministic and intended to gate PRs.
2. Tier-2 nightly is report/artifact oriented and broadens preset/width coverage.
3. Keep `docs_tmp/` artifacts out of commits.

## Promotion Rubric (Warn/Report-Only -> Strict)

Policy:
1. Strict gates must remain minimal and stable. Any new strict gate must have:
   - deterministic output paths
   - reproducible local command
   - clear false-positive handling
2. Report-only lanes are allowed to be noisy, but must be measurable and summarized.

Promotion from report-only to strict requires:
1. The lane is green for at least 10 consecutive CI runs on `main`.
2. Any known "expected mismatch" cases are either resolved or explicitly excluded from the lane.
3. The lane has an explicit owner and rollback switch (or can be reverted cleanly).

## Expected Mismatches (Report-Only Policy)

Report-only lanes may show failures that are acceptable as visibility signals, but only if:
1. They are explicitly documented here, and
2. They do not affect the canonical strict canary.

Currently documented report-only mismatch class:
1. `ascii-no-color` lanes can fail ANSI/style requirements for scenarios that require ANSI output.
   - Why: some style assertions (`require_ansi`, diff backgrounds, etc.) are intentionally incompatible with `NO_COLOR=1`.
   - Action: treat as visibility; do not promote to strict until the lane is made semantically meaningful (either adjust scenario style requirements or drop the incompatible combos).
