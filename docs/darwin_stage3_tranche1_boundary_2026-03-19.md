# DARWIN Stage-3 Tranche-1 Boundary

Date: 2026-03-19
Status: active
Scope: Stage-3 tranche-1 substrate uptake

## Authorized scope

- `OptimizationSubstrateV1` uptake
- narrow `ExecutionPlanV1` consumption
- `EvaluatorPackV1` plus `BudgetEnvelope` closure
- `DecisionLedgerV1` narrowing
- bounded canaries on `lane.repo_swe` and `lane.systems`
- secondary confirmation on `lane.scheduling`
- audit-only posture on `lane.atp`

## Explicit exclusions

- no broad real-inference campaigns
- no async expansion
- no new runtime truth
- no archive-as-truth
- no broad lane rollout
- no public or superiority packaging

## Boundary marker

The current tranche boundary is the Stage-3 substrate-backed mutation canary:

- `lane.repo_swe` is the primary substrate proving lane
- `lane.systems` is the first generality lane
- `ExecutionPlan` consumption remains limited to the narrow binding subset
