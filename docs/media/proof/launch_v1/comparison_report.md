# tmux run vs golden comparison

- run: `/shared_folders/querylake_server/ray_testing/ray_SCE/docs_tmp/tmux_captures/scenarios/codex/e2e_compact_semantic_v1/20260210-131357`
- golden: `/shared_folders/querylake_server/ray_testing/ray_SCE/docs_tmp/tmux_captures/goldens/codex/e2e_compact_semantic_v1/20260209-181804`
- scenario: `codex/e2e_compact_semantic_v1`
- provider: `codex`
- overall: `fail`

| category | status | key metrics |
|---|---|---|
| layout | fail | drift=0.7458, frames(run/golden)=45/241, missing=0 |
| semantic | pass | misses=0, semantic_failures=0, action_delta=0 |
| provider | pass | token_drifts=0, compaction_missing=0 |
| pixel | skip | enabled=False, index=45, change_ratio=n/a, mean_abs=n/a |

## layout failures
- layout_drift_score=0.7458 > max_drift_score=0.24
- max_line_delta=10 > max_max_line_count_delta=2

## semantic failures
- none

## provider_checks failures
- none

## pixel failures
- none

