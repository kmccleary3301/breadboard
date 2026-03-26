# DARWIN Stage-5 Secondary-Lane Slice

Date: 2026-03-21
Status: bounded Systems secondary-lane slice
References:
- `docs/darwin_stage5_execution_plan_2026-03-20.md`
- `docs/darwin_stage5_tranche1_hard_gate_2026-03-21.md`
- `breadboard_ext/darwin/stage5.py`
- `scripts/run_darwin_stage5_compounding_pilot_v0.py`
- `scripts/run_darwin_stage5_systems_compounding_pilot_v0.py`

## Purpose

This slice is the smallest authorized Stage-5 extension beyond Repo_SWE tranche-1.

It keeps Repo_SWE as the primary Stage-5 lane and allows Systems to participate as a bounded secondary lane under the same `cold_start`, `warm_start`, and `family_lockout` protocol.

## Included work

1. extend `SearchPolicyV2` authorization from Repo_SWE-only to Repo_SWE + Systems
2. run a bounded Systems compounding pilot using the promoted Systems policy family
3. preserve current Stage-4 and Stage-5 runtime-truth, matched-budget, and live/scaffold boundaries
4. keep Harness in watchdog-only role

## Excluded work

- no broad Stage-5 transfer matrix
- no Scheduling consumer slice
- no family composition
- no additional runtime ownership or async work
- no broad lane rollout

## Hard boundary

If Systems cannot run the same typed compounding protocol without widening runtime truth, Stage 5 should hold at Repo_SWE-primary compounding rather than widening further.
