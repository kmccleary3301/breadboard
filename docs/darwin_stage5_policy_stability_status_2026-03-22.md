# DARWIN Stage-5 Policy Stability Status

Date: 2026-03-22
Status: policy-stability deadband and timeout-hardening slice landed
References:
- `docs/darwin_stage5_policy_stability_slice_2026-03-22.md`
- `artifacts/darwin/stage5/policy_stability/policy_stability_v0.json`

## What landed

- a derived policy-stability report over the repeated multi-lane bundle
- bounded Stage-5 compounding deadband handling for small runtime/cost deltas
- Stage-4 provider timeout fallback hardening for repeated live Stage-5 rounds
- lane-level stability classes
- round-level reuse/no-lift/flat consistency
- policy review conclusions per lane

## Current stability result

- Repo_SWE:
  - `mixed_negative`
  - `1` reuse-lift, `5` flat, `2` no-lift across two rounds
  - round 1: `1` reuse-lift, `3` flat, `0` no-lift
  - round 2: `0` reuse-lift, `2` flat, `2` no-lift
  - policy review: `continue`
- Systems:
  - `mixed_positive`
  - `7` reuse-lift vs `5` no-lift across two rounds
  - round 1: `3` reuse-lift vs `3` no-lift
  - round 2: `4` reuse-lift vs `2` no-lift
  - policy review: `continue`

## Interpretation

The repeated multi-lane compounding surface is now better characterized:

- Repo_SWE is no longer being overstated by tiny runtime/cost deltas; most of its current surface is flat rather than strongly negative
- Systems remains the cleaner current compounding lane under the same bounded protocol
- the deadband changed the interpretation materially enough to matter: Repo_SWE is weak and mixed, but not currently a clear "tighten harder" lane

That is a useful result because it sharpens the Stage-5 question from "is compounding real?" to "which lane actually benefits from further stability pressure?" without overstating either lane.

## Route/economics note

The stability report remains caveated by the same provider-economics behavior:

- OpenRouter remains preferred in code
- direct OpenAI fallback still occurs after `openrouter_http_401`
- repeated live runs now also degrade cleanly after OpenRouter timeouts instead of aborting the tranche run
- OpenRouter timeout handling is now capped so repeated Stage-5 rounds do not stall behind one slow provider edge

## What this authorizes next

- keep the Stage-5 policy layer focused on stability, not transfer
- stop treating small Repo_SWE runtime/cost differences as compounding evidence
- keep Repo_SWE bounded and move its next adjustment toward family-level or comparison-protocol work, not more raw repetition
- keep Systems as the clearer secondary signal lane for now
- avoid claiming stable scalable compounding until the policy/review layer is cleaner
