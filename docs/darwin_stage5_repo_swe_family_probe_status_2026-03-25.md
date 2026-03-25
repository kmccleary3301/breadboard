# DARWIN Stage-5 Repo_SWE Family Probe Status

Date: 2026-03-25
Status: landed
References:
- `docs/darwin_stage5_repo_swe_family_probe_slice_2026-03-25.md`
- `artifacts/darwin/stage5/multilane/multilane_compounding_v0.json`
- `artifacts/darwin/stage5/policy_stability/policy_stability_v0.json`

## What landed

- Repo_SWE can now switch from the promoted topology family to the withheld tool-scope family under a bounded Stage-5 probe
- the probe is activated only from the policy-stability surface
- the live multi-lane Stage-5 bundle was rerun with the new Repo_SWE family choice

## Current result

- Repo_SWE:
  - operator under probe: `mut.tool_scope.add_git_diff_v1`
  - `4` reuse-lift
  - `2` flat
  - `6` no-lift
  - stability: `mixed_negative`
  - policy review: `continue`
- Systems:
  - unchanged lane family policy
  - `7` reuse-lift
  - `0` flat
  - `5` no-lift
  - stability: `mixed_positive`
  - policy review: `continue`

## Interpretation

The Repo_SWE family probe improved interpretability.

- the prior topology probe had become mostly flat
- the new tool-scope probe produces more directional outcomes
- Repo_SWE still does not cross into positive stability

This means the family-level adjustment was justified, but it did not yet solve the Stage-5 Repo_SWE compounding problem.

## Route/economics note

- OpenRouter remains preferred in code
- direct OpenAI fallback still occurs after `openrouter_http_401`
- repeated Stage-5 runs now also survive OpenRouter timeout cases without aborting the bundle
