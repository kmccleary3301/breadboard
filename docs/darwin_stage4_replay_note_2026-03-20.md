# DARWIN Stage-4 Replay Note

Date: 2026-03-20
Status: bounded replay check after deep live search
References:
- `artifacts/darwin/stage4/deep_live_search/replay_checks_v0.json`
- `artifacts/darwin/stage4/deep_live_search/strongest_families_v0.json`

## Checked rows

- `lane.repo_swe` / `mut.tool_scope.add_git_diff_v1`
- `lane.systems` / `mut.policy.shadow_memory_enable_v1`

## Result

Both rows replayed with:

- `verifier_status=passed`
- `primary_score=1.0`
- `replay_supported=true`

## Interpretation

The current strongest family on each primary lane is not a one-off failure artifact.

This is still bounded replay:

- one strongest family per primary lane
- no broad replay sweep
- no promotion decision made yet
