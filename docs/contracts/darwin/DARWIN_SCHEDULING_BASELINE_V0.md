# DARWIN Scheduling Baseline V0

Phase-1 treats `lane.scheduling` as a bounded deterministic scheduling lane with an exact checker.

## Baseline evaluator slice

- scenario pack: `docs/contracts/darwin/fixtures/scheduling_scenario_pack_v0.json`
- baseline runner: `scripts/run_darwin_scheduling_lane_baseline_v0.py`
- exact checker: brute-force optimal schedule inside the runner

## Strategies used in the current tranche

- baseline: `deadline_first`
- mutations:
  - `value_density`
  - `slack_then_value`

## Why this slice

- deterministic
- exact-checkable
- cheap enough for repeated replay and promotion audit
- cross-domain relative to ATP / repo_swe

## Phase-1 boundary

This is not yet a simulator-rich or stochastic scheduling benchmark. It is a promotion-capable exact-check lane.
