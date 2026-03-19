# DARWIN Stage-3 Tranche-1 Uptake Gate

Date: 2026-03-19

- [x] First Stage-3 canary landed on `codex/darwin-stage3-plan-20260319`
- [x] `OptimizationTarget` templates exist for `lane.repo_swe` and `lane.systems`
- [x] `ExecutionPlan` consumption is limited to:
  - `bindings.command`
  - `bindings.cwd`
  - `bindings.out_dir`
- [x] `lane.harness` and `lane.repo_swe` proving runs consume only the narrow allowed plan fields
- [x] Runtime truth remains singular
- [x] Systems remains outside runtime-heavy proving
- [x] Budget-envelope helper exists on proving-lane evaluator packs
- [x] Optimize-object mapping note written
- [x] Uptake review written
- [x] Review outcome is explicit

## Gate decision

The first Stage-3 uptake gate is **passed**.

## Authorized next move

Proceed to the next limited Stage-3 substrate-uptake slice:

- deepen optimize-substrate uptake
- strengthen evaluator/budget telemetry closure
- narrow decision-ledger truth

## Not authorized by this gate

- broad real-inference campaigns
- systems runtime-heavy proving
- async search
- archive-as-truth changes
- new runtime truth

