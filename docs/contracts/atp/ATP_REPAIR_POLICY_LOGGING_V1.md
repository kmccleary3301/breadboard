# ATP Repair Policy Logging v1

Date: 2026-02-24

## Purpose

Record repair-operator selection decisions so repair behavior is auditable, reproducible, and ablation-ready.

## Decision Log Contract

Schema:

- `docs/contracts/atp/schemas/atp_repair_policy_decision_v1.schema.json`

Primary fields:

- `diagnostic_class`
- `selected_operator`
- `ablation_repair_off`
- `features` (attempt index, budget remaining, goals estimate)
- `justification`

## Operator Set (v1)

- `repair_minimal_patch`
- `repair_local_rewrite`
- `repair_goal_decompose`
- `repair_retrieve_more`
- `repair_change_strategy`
- `repair_off` (ablation mode)

## Runtime Emission Path

Current ATP decomposition runner emits:

- artifact: `repair_policy_log.json`
- report fields:
  - `repair_policy_log_path`
  - `repair_policy_validation_ok`
  - `repair_policy_validation_errors`
  - `repair_off`
  - `repair_attempt_count`
  - `repair_success_count`
  - `repair_success_rate`

Script:

- `scripts/run_atp_retrieval_decomposition_loop_v1.py`

Validator:

- `scripts/check_atp_repair_policy_log_v1.py`

## Ablation Toggle

CLI flag:

- `--repair-off`

Effect:

- skips repair node generation,
- logs `selected_operator=repair_off`,
- keeps replay/trace deterministic for ablation comparisons.

## Minimal Success Metrics (v1)

1. `repair_policy_validation_ok == true`
2. `repair_attempt_count > 0` in normal mode
3. `repair_success_rate` is explicitly reported
4. `--repair-off` mode runs and logs policy decisions without schema violations
