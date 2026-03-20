# DARWIN Stage-3 Tranche-1 Budget and Ledger Status

Date: 2026-03-19
Status: telemetry and decision-truth tightening landed
References:
- `docs/darwin_stage3_tranche1_uptake_review_2026-03-19.md`
- `docs/darwin_stage3_tranche1_uptake_gate_2026-03-19.md`

## What landed

- `BudgetEnvelope` is now a structured evaluator-pack field rather than an unstructured helper payload
- proving-lane evaluator packs now record:
  - budget class
  - wall-clock
  - normalized token counts
  - cost estimate
  - cost-classification
  - comparison class
  - replication / control reserve fractions
- `EvolutionLedger` now declares an explicit `decision_truth_scope`

## Why this matters

This is the first tightening step after the uptake gate:

- evaluator closure is moving toward comparison-ready telemetry
- decision truth is becoming more explicit about what it owns
- archive remains derived-only

## Boundary

This still does not authorize:

- broad real-inference spend
- systems runtime-heavy proving
- async search
- new runtime truth

