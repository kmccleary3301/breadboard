# DARWIN Stage-3 Bounded Real-Inference Entry Conditions

Date: 2026-03-19
Status: tranche-2 entry conditions defined

## Authorized first lanes

- primary: `lane.repo_swe`, `lane.systems`
- watchdog/control: `lane.harness`
- secondary confirmation: `lane.scheduling`
- audit: `lane.atp`

## Initial search space

- bounded operator families only
- bounded mutable loci only
- fixed campaign sizes
- replay reserve and control reserve remain mandatory

## Initial campaign limits

- no async
- no broad lane spread
- small repeated campaigns only
- cost ceilings remain tranche-local and reviewable

## Abort conditions

- evaluator-pack closure missing
- budget-envelope telemetry missing
- runtime-truth drift
- parity or replay regressions
- invalid-comparison pressure overwhelms valid comparisons
