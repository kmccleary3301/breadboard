# DARWIN Stage-4 Transfer Comparability Note

Date: 2026-03-20
Status: retained/failure transfer comparability note
References:
- `artifacts/darwin/stage4/family_program/bounded_transfer_outcomes_v0.json`
- `artifacts/darwin/stage4/family_program/family_registry_v0.json`

## Retained transfer

- repo_swe topology -> systems

This transfer remains comparability-valid because:

- the source family is canonical in the current program
- the target lane already has valid positive topology evidence under the same bounded Stage-4 comparison discipline
- the transfer is lane-specific and does not widen beyond systems

## Failure and invalidity cases

- repo_swe tool-scope -> scheduling: `unsupported_lane_scope`
- systems policy -> scheduling: `evaluator_scope_mismatch`

These failures are structurally meaningful. They mark the current family boundary instead of being random negative outcomes.
