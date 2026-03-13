# ATP Phase 1 Completion Gate — 2026-03-13

## Purpose

This document defines the stopping condition for the current ATP Phase 1 comparison program against the maintained Hilbert fork.

Phase 1 is complete only when the program has:

- stable tranche construction policy
- canonical aggregate reporting
- explicit repair/no-repair accounting
- BreadBoard and Hilbert spend visibility
- enough valid tranche breadth to support a real program-level claim
- at least one replay audit

## Completion Criteria

### Gate A — canonical evidence lock

Required:

- canonical baseline index exists
- aggregate scoreboard exists
- evaluation memo exists

Status:

- met

Artifacts:

- `artifacts/benchmarks/hilbert_comparison_packs_v2/canonical_baseline_index_v1.json`
- `artifacts/benchmarks/hilbert_comparison_packs_v2/scoreboard_v1.json`
- `docs/atp_hilbert_phase1_eval_memo_2026-03-13.md`

### Gate B — tranche governance

Required:

- tranche selection policy exists
- invalid extract ledger exists
- maintained-Hilbert runbook exists

Status:

- met

Artifacts:

- `docs/contracts/benchmarks/ATP_HILBERT_TRANCHE_SELECTION_POLICY_V1.md`
- `artifacts/benchmarks/hilbert_comparison_packs_v2/invalid_extract_ledger_v1.json`
- `docs/ATP_HILBERT_MAINTAINED_RUNBOOK_V1_2026-03-13.md`

### Gate C — cost visibility

Required:

- maintained-Hilbert exact spend is present in program rollup
- BreadBoard estimated spend backfill exists

Status:

- met

Artifacts:

- `artifacts/benchmarks/hilbert_comparison_packs_v2/bb_spend_backfill_v1.json`
- `artifacts/benchmarks/hilbert_comparison_packs_v2/scoreboard_v1.json`

### Gate D — arm separation

Required:

- baseline-only versus focused-repaired versus split-rollup versus stress are explicit
- at least one no-repair comparator slice exists
- repair intensity is quantified

Status:

- met

Artifacts:

- `artifacts/benchmarks/hilbert_comparison_packs_v2/arm_audit_v1.json`
- `artifacts/benchmarks/hilbert_comparison_packs_v2/no_repair_slice_v1.json`
- `artifacts/benchmarks/hilbert_comparison_packs_v2/repair_intensity_v1.json`

### Gate E — breadth

Required:

- at least `10` headline packs (`canonical_primary`, `canonical_rollup`, or `boundary_stress`)

Status:

- met

Current state:

- headline packs: `12`

### Gate F — replay stability

Required:

- at least one easy canonical replay audit
- at least one medium canonical replay audit

Status:

- met

Artifacts:

- `docs/atp_hilbert_replay_audit_status_2026-03-13.md`
- `artifacts/benchmarks/hilbert_replay_audit_v1/replay_audit_summary_v1.json`

## Current overall gate state

Met:

- Gate A
- Gate B
- Gate C
- Gate D
- Gate E
- Gate F

Not met:

- none

## Practical read

The ATP comparison machinery is already mature enough for continued tranche expansion.

Phase 1 is now gate-complete: headline breadth is above threshold and replay stability has been checked on one easy and one medium canonical pack.

## Immediate closeout path

Phase 1 closeout is now bookkeeping only:

1. keep the replay audit artifacts with the canonical pack set
2. preserve the current rollup as the Phase 1 baseline
3. merge the ATP branch cleanly
