# TUI Tier-1 Goldens Runbook (U1/U2/U3)

This document defines the Tier-1 deterministic gates and the operator workflows for:
1. report generation,
2. strict gating, and
3. explicit blessing.

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

1. `/.github/workflows/tui-u1-goldens.yml`
2. `/.github/workflows/tui-u2-goldens.yml`
3. `/.github/workflows/tui-u3-goldens.yml`
4. `/.github/workflows/tui-goldens-tier2-nightly.yml`

Tier-1 report jobs run matrix coverage for:
1. `breadboard_default` + `claude_code_like`
2. `unicode-color` + `ascii-no-color`

Tier-1 gate jobs run strict checks on canonical baseline:
1. preset `breadboard_default`
2. unicode-color mode

## Local Commands

## One-command Tier-1 rerun (report matrix + strict gates)

```bash
./scripts/run_tui_tier1_local.sh
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
