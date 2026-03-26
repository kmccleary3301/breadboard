# DARWIN Stage-5 Scaled Compounding Status

Date: 2026-03-25
Status: tranche-3 scaled compounding landed
References:
- `artifacts/darwin/stage5/scaled_compounding/scaled_compounding_v0.json`
- `artifacts/darwin/stage5/compounding_rate/compounding_rate_v0.json`
- `artifacts/darwin/stage5/family_registry/family_registry_v0.json`
- `artifacts/darwin/stage5/bounded_transfer/bounded_transfer_outcomes_v0.json`
- `artifacts/darwin/stage5/composition_canary/composition_canary_v0.json`

## What landed

- a round-series scaled compounding runner
- compounding-rate and family-reuse surfaces
- a Stage-5 family registry
- a bounded transfer matrix
- an explicit composition canary decision
- replay, scorecard, memo, and verification bundle surfaces

## Current read

- `lane.systems`
  - weight: `primary_proving_lane`
  - rounds: `2`
  - aggregate reuse rate: `0.25`
  - state: `active_proving`
- `lane.repo_swe`
  - weight: `challenge_lane`
  - rounds: `2`
  - aggregate reuse rate: `0.1875`
  - state: `challenge_only`
- third-family decision: `hold_two_family_center`
- retained bounded transfer count: `1`
- composition canary: `composition_not_authorized`

## Interpretation

Stage 5 is now beyond proving-repair work.

The program has a scaled compounding surface, one retained bounded transfer, and an explicit composition no-go. That is enough to move the program into late-stage comparative territory rather than leaving it in Tranche-2 proving hygiene.
