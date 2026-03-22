# DARWIN Stage-5 Policy Stability Status

Date: 2026-03-22
Status: policy-stability report landed
References:
- `docs/darwin_stage5_policy_stability_slice_2026-03-22.md`
- `artifacts/darwin/stage5/policy_stability/policy_stability_v0.json`

## What landed

- a derived policy-stability report over the repeated multi-lane bundle
- lane-level stability classes
- round-level reuse/no-lift consistency
- policy review conclusions per lane

## Current stability result

- Repo_SWE:
  - `mixed_positive`
  - `6` reuse-lift vs `6` no-lift across two rounds
  - round 1 mixed, round 2 mixed
- Systems:
  - `mixed_positive`
  - `5` reuse-lift vs `3` no-lift across two rounds
  - round 1 mixed, round 2 mixed

## Interpretation

The repeated multi-lane compounding surface is now better characterized:

- Repo_SWE is now tightened enough to move into mixed-positive repeated behavior
- Systems remains the stronger aggregate lane, but still not round-dominant

That is a useful result because it sharpens the Stage-5 question from "is compounding real?" to "where is compounding stabilizing, and where is it not?" and shows the Repo_SWE tightening actually changed the lane behavior.

## Route/economics note

The stability report remains caveated by the same provider-economics behavior:

- OpenRouter remains preferred in code
- direct OpenAI fallback still occurs after `openrouter_http_401`

## What this authorizes next

- keep the Stage-5 policy layer focused on stability, not transfer
- continue the tightened Repo_SWE selection path before widening again
- continue bounded multi-lane protocol work
- avoid claiming stable scalable compounding until the policy/review layer is cleaner
