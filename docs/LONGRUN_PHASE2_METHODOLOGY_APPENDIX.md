# LongRun Phase2 Methodology Appendix

This appendix documents the deterministic and live-evaluation methodology used for the LongRun/Ralph phase-2 program.

## 1) Goals

1. Measure boundedness and stability behavior of LongRun controller policies.
2. Preserve parity-safe defaults and replay-auditable artifacts.
3. Separate low-cost deterministic checks from spend-bearing live probes.

## 2) Evaluation Layers

## Layer A: Deterministic policy matrix (no provider spend)

Inputs:

1. `scripts/run_longrun_phase1_pilot.py`
2. `config/longrun/phase1_scenario_contract_v1.json`

Outputs:

1. `longrun_phase1_pilot_v1.json`
2. `longrun_phase1_pilot_summary.md`
3. `longrun_phase1_scenario_contract_report.json`

Purpose:

1. Verify stop semantics, retry/rollback boundedness, and scenario-level invariants.
2. Detect regressions in controller policy behavior before any live spend.

## Layer B: Strict gate with parity audit (no provider spend)

Inputs:

1. deterministic pilot payload,
2. parity-disabled audit (`scripts/audit_longrun_parity_disabled.py`),
3. go/no-go evaluator (`scripts/evaluate_longrun_phase2_go_no_go.py`).

Outputs:

1. go/no-go report,
2. trend history updates,
3. trend markdown summaries.

Purpose:

1. Block unsafe policy changes from promotion.
2. Keep longrun disabled-by-default parity surfaces intact.

## Layer C: Spend-capped live pilot

Inputs:

1. `scripts/run_longrun_phase2_live_pilot.py`,
2. task fixtures in `config/longrun/`,
3. explicit token/cost caps and per-arm timeout.

Outputs:

1. live JSON artifact with per-arm telemetry,
2. markdown summary with boundedness and runtime diagnostics.

Purpose:

1. Validate real provider behavior under strict caps.
2. Capture practical runtime failure classes without destabilizing baseline lanes.

## 3) Contract checks

Scenario contract checks now include:

1. required scenario IDs,
2. required summary fields per arm,
3. allowed stop-reason class validation,
4. expected stop-class presence per scenario/arm.

Artifact shape checks for live pilot include:

1. usage fields,
2. cost fields,
3. per-arm telemetry (status, failure class, guard counters, timeout flag).

## 4) Reproducibility controls

1. Deterministic pilot matrix uses fixed scenario logic and fixed run counts.
2. Live pilot uses explicit caps:
   1. max pairs,
   2. max total tokens,
   3. max total estimated cost,
   4. per-arm timeout.
3. Trend reports are generated from machine-readable history updates.

## 5) Promotion approach

1. Run deterministic and strict no-spend lanes first.
2. Use warn-only trend lane for drift visibility before strict promotion.
3. Run live pilot only when policy and deterministic gates are stable.
