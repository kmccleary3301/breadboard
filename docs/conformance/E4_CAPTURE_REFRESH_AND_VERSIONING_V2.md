# E4 Capture Refresh + Versioned Config Workflow (V2)

This playbook refreshes target-harness captures and preserves prior E4 baselines
by creating side-by-side, versioned E4 config files.

## Goals

- Capture fresh goldens for Claude Code, OpenCode, and Codex CLI.
- Preserve previous E4 config files untouched.
- Add new versioned E4 config rows to `config/e4_target_freeze_manifest.yaml`.

## 1) Capture fresh harness goldens

Recommended (single orchestration command):

```bash
cd breadboard_repo
bash scripts/capture_e4_target_goldens.sh \
  --run-id-prefix e4_refresh_YYYYMMDD_bundle \
  --codex-model gpt-5-codex \
  --claude-model haiku \
  --opencode-model openai/gpt-5.1-codex-mini \
  --include-codex-subagents
```

Optional Codex MVI refresh lane (same tranche):

```bash
cd breadboard_repo
bash scripts/capture_codex_golden.sh \
  --scenario codex_mvi_patch_v2 \
  --prompt-file misc/codex_cli_tests/prompts/codex_mvi_patch_prompt.md \
  --model gpt-5-codex \
  --run-id e4_refresh_YYYYMMDD_bundle_codex_mvi_patch_v2
```

### Codex CLI

```bash
cd breadboard_repo
bash scripts/capture_codex_golden.sh \
  --scenario codex_subagent_sync_v1 \
  --prompt-file misc/codex_cli_tests/prompts/codex_subagent_sync_prompt.md \
  --model gpt-5-codex
```

```bash
cd breadboard_repo
bash scripts/capture_codex_golden.sh \
  --scenario codex_subagent_async_v1 \
  --prompt-file misc/codex_cli_tests/prompts/codex_subagent_async_prompt.md \
  --model gpt-5-codex
```

### Claude Code

```bash
cd breadboard_repo
bash scripts/capture_claude_golden.sh \
  --scenario claude_e4_refresh_ping_v1 \
  --prompt "Reply exactly with: claude-e4-refresh-ready" \
  --model haiku
```

### OpenCode

```bash
cd breadboard_repo
bash scripts/capture_opencode_golden.sh \
  --scenario opencode_e4_refresh_ping_v1 \
  --prompt "Reply exactly with: opencode-e4-refresh-ready" \
  --model openai/gpt-5.1-codex-mini
```

## 2) Create versioned E4 config snapshots (side-by-side)

Use a snapshot tag that encodes the harness target version/date.

```bash
cd breadboard_repo
python scripts/create_versioned_e4_snapshot_configs.py \
  --snapshot-tag codex0_1050_claude2_0_72_opencode1_2_6_20260304
```

This creates files such as:

- `agent_configs/codex_cli_gpt5_e4_live__codex0_1050_claude2_0_72_opencode1_2_6_20260304.yaml`
- `agent_configs/claude_code_haiku45_e4_replay__codex0_1050_claude2_0_72_opencode1_2_6_20260304.yaml`
- `agent_configs/opencode_e4_...__codex0_1050_claude2_0_72_opencode1_2_6_20260304.yaml`

and appends matching rows in:

- `config/e4_target_freeze_manifest.yaml`

## 3) Validate manifest integrity

```bash
cd breadboard_repo
python scripts/check_e4_target_freeze_manifest.py --json
python scripts/check_e4_target_freeze_manifest.py --strict-evidence --json
```

## 4) Optional: refresh tmux codex/claude evidence lanes

If updating the nightly-provider tmux semantic captures for E4:

- Follow `docs/conformance/E4_CODEX_LIVE_REFRESH_RUNBOOK.md` for Codex.
- Use the same pattern for Claude with `nightly_provider/claude_e2e_compact_semantic_v1`.

## Notes

- This workflow does not delete or overwrite previous E4 config files.
- Existing non-versioned E4 rows remain valid for historical reproducibility.

## Post-Restore Strict Probe Checklist (2026-03-04)

Use this checklist when finalizing a restored-tree E4 refresh tranche.

- [x] One-command strict replay probe target added:
  - `make e4-postrestore-strict-probe`
- [x] Capture refreshed for Codex (`codex_e4_refresh_ping_v1`, `codex_subagent_sync_v1`, `codex_subagent_async_v1`).
- [x] Codex MVI refresh lane captured (`codex_mvi_patch_v2`).
- [x] Capture refreshed for Claude (`claude_e4_refresh_ping_v1`, `claude_compact_semantic_v2`).
- [x] Capture refreshed for OpenCode (`opencode_e4_refresh_ping_v1`, `opencode_compact_semantic_v2`).
- [x] Side-by-side snapshot configs generated with explicit multi-harness tag:
  - `codex0_1050_claude2_0_72_opencode1_2_6_20260304`
- [x] Manifest checks green:
  - `python scripts/check_e4_target_freeze_manifest.py --json`
  - `python scripts/check_e4_target_freeze_manifest.py --strict-evidence --json`
- [x] Codex strict replay probe green:
  - `artifacts/parity_runs/codex_capture_refresh_20260304_postfix/parity_summary.json` (3/3 passed)
- [x] Claude/OpenCode strict replay probe green:
  - `artifacts/parity_runs/claude_opencode_replay_probe_strict_20260304_v2/parity_summary.json` (4/4 passed)

Reference status log:

- `docs_tmp/cli_phase_3/E4_CAPTURE_REFRESH_20260304_PROGRESS.md`
