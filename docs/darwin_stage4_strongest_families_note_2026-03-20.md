# DARWIN Stage-4 Strongest Families Note

Date: 2026-03-20
Status: strongest-family summary after repeated live rounds
References:
- `artifacts/darwin/stage4/deep_live_search/strongest_families_v0.json`
- `artifacts/darwin/stage4/deep_live_search/family_prior_stability_v0.json`
- `artifacts/darwin/stage4/deep_live_search/operator_ev_by_round_v0.json`

## Strongest families so far

### `lane.repo_swe`

- `mut.tool_scope.add_git_diff_v1`
  - valid comparisons: `5`
  - positive power signals: `4`
  - signal rate: `0.8`
- `mut.topology.single_to_pev_v1`
  - valid comparisons: `5`
  - positive power signals: `4`
  - signal rate: `0.8`

### `lane.systems`

- `mut.policy.shadow_memory_enable_v1`
  - valid comparisons: `5`
  - positive power signals: `4`
  - signal rate: `0.8`
- `mut.topology.single_to_pev_v1`
  - valid comparisons: `5`
  - positive power signals: `3`
  - signal rate: `0.6`

## Interpretation

Stage 4 now has more than one family worth promotion review.

The evidence is still bounded:

- all current positive signals are retained-score efficiency signals
- route economics are real but still provider-mix-limited
- replay posture still has to be checked before promotion claims
