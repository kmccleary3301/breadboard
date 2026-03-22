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
  - `stable_negative`
  - `1` reuse-lift vs `7` no-lift across two rounds
  - round 1 negative, round 2 negative
- Systems:
  - `mixed_positive`
  - `4` reuse-lift vs `4` no-lift across two rounds
  - round 1 mixed, round 2 mixed

## Interpretation

The repeated multi-lane compounding surface is now better characterized:

- Systems is the stronger lane on aggregate balance, but not yet round-dominant
- Repo_SWE remains unstable in the repeated view

That is a useful result because it sharpens the Stage-5 question from "is compounding real?" to "where is compounding stabilizing, and where is it not?"

## Route/economics note

The stability report remains caveated by the same provider-economics behavior:

- OpenRouter remains preferred in code
- direct OpenAI fallback still occurs after `openrouter_http_401`

## What this authorizes next

- keep the Stage-5 policy layer focused on stability, not transfer
- continue bounded multi-lane protocol work
- avoid claiming stable scalable compounding until the policy/review layer is cleaner
