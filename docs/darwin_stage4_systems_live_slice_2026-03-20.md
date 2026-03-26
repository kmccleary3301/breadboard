# DARWIN Stage-4 Systems Live Slice

Date: 2026-03-20
Status: completed bounded systems live-provider slice
Scope: first narrow `lane.systems` live-provider pilot under Stage-4 matched-budget and EV rules
References:
- `docs/darwin_stage4_execution_plan_2026-03-19.md`
- `docs/darwin_stage4_live_economics_status_2026-03-20.md`
- `scripts/run_darwin_stage4_systems_live_pilot_v0.py`
- `scripts/build_darwin_stage4_systems_ev_bundle_v0.py`
- `artifacts/darwin/stage4/systems_live/live_economics_pilot_v0.json`
- `artifacts/darwin/stage4/systems_live/matched_budget_comparisons_v0.json`
- `artifacts/darwin/stage4/systems_live/operator_ev_report_v0.json`
- `artifacts/darwin/stage4/systems_live/topology_ev_report_v0.json`

## Purpose

This slice proves that Stage-4 live-provider discipline generalizes beyond `lane.repo_swe`.

It does not widen Stage 4 into broad live campaigns.

## Boundaries

- one systems control arm
- two systems mutation arms
- no budget-class mutation
- no new primitive family
- no transfer/promotion work

## Selected systems arm set

- control: `baseline_seed`
- mutation: `mut.topology.single_to_pev_v1`
- mutation: `mut.policy.shadow_memory_enable_v1`

## Result

The first systems pilot now has:

- `execution_mode=live`
- provider-backed cost semantics
- valid matched-budget comparisons on all systems mutation rows
- bounded positive power signals for both selected systems operator families

## Boundary reminder

This slice does not establish:

- broad systems-lane compounding
- score-improving search on systems
- transfer or multi-family promotion
