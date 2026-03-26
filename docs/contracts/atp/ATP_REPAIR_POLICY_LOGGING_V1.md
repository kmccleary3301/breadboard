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

Current merged ATP formal workflows center on:

- `scripts/run_bb_formal_pack_v1.py`
- `scripts/run_bb_atp_adapter_slice_v1.py`

These runners already emit ATP-facing diagnostics and manifest-driven formal workflow artifacts. They are the right current reference points for repair-policy integration work on `main`.

Earlier docs referenced a standalone ATP retrieval/decomposition runner and a dedicated repair-policy log validator. Those were part of an older tranche shape and are not the maintained entrypoints in the current merged ATP program.

For current repair-policy work, the practical expectation is:

- normalize failures into ATP diagnostic classes
- make repair or fallback decisions explicit in the ATP-facing workflow artifacts
- keep replay- and comparison-friendly traces around the proving run
- use the current formal-pack and adapter-slice scripts as the operational proving surfaces

## Ablation Toggle

The exact ablation toggle is now workflow-specific rather than centralized in one standalone ATP runner. When adding or evolving repair-policy behavior on current `main`, keep the ablation requirement itself stable:

- there must be a way to disable the repair operator path cleanly
- the no-repair path must remain replayable and comparable
- the emitted ATP-facing artifacts must make the ablation visible

## Minimal Success Metrics (v1)

1. repair or fallback behavior is explicit in ATP-facing artifacts rather than implied by raw logs
2. the no-repair or reduced-repair path is available for ablation comparisons
3. proving runs remain replayable and comparable under both normal and ablated modes
4. ATP-facing workflow artifacts remain schema-stable enough for tranche comparison and rollup work
