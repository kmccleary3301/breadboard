# DARWIN Phase-2 Compute-Normalized Tranche Execution Plan

Date: 2026-03-18
Status: selected planning tranche
Primary issue: `breadboard_repo_darwin_phase1_20260313-8xc`
Scope: additive-only comparative normalization after tranche-1 hard-gate review

## 1. Purpose

This document defines the first scoped execution tranche after DARWIN Phase-2 tranche 1.

The purpose of this tranche is:

- strengthen comparative interpretation over existing DARWIN artifacts
- tighten compute-normalization semantics and cost-accounting language
- improve scorecard usefulness without creating new runtime truth

This tranche is explicitly downstream of:

- `docs/darwin_phase2_tranche1_execution_plan_2026-03-14.md`
- `docs/darwin_phase2_tranche1_hard_gate_review_2026-03-18.md`
- `docs/darwin_phase2_next_tranche_decision_2026-03-18.md`

## 2. Why this tranche is next

Tranche 1 already established:

- compiled execution surfaces
- compiled policy and evaluator surfaces on proving lanes
- minimal reconstruction-first decision lineage

What is still weak is not artifact availability. It is comparative interpretation.

The current compute-normalized layer is useful as a Phase-1 companion, but it is still too thin for Phase-2 decision support because it:

- reduces normalization mostly to score-per-second
- does not separate evaluator runtime from broader cost semantics clearly enough
- does not carry enough interpretation policy into the scorecard surface
- is not yet strong enough to support the later broader transfer/lineage tranche

## 3. Tranche mission

The mission of this tranche is:

> produce a stricter compute-normalized comparative surface over existing DARWIN outputs, with clearer cost semantics and tighter interpretation policy, while remaining fully additive-only.

## 4. Boundary

### Must do

- define stronger compute-normalized semantics
- tighten cost-accounting fields and interpretation policy
- upgrade the comparative scorecard surface
- improve dossier support from the stronger normalized view
- prove the new surfaces primarily on `lane.harness` and `lane.repo_swe`

### Must not do

- no runtime consumption of tranche-1 compiled artifacts
- no new BreadBoard kernel-truth primitive
- no broader transfer-family rollout
- no new lane expansion
- no external-safe evidence packet work
- no async dependency
- no ATP-first proving posture

## 5. Primary proving lanes

Primary proving lanes:

- `lane.harness`
- `lane.repo_swe`

Secondary confirmation or audit only:

- `lane.scheduling`
- `lane.atp`

Not primary in this tranche:

- `lane.systems`
- `lane.research`

## 6. Current weakness in the existing companion

Current canonical companion:

- `docs/contracts/darwin/DARWIN_COMPUTE_NORMALIZED_SCORECARD_V1.md`
- `artifacts/darwin/scorecards/compute_normalized_view_v1.json`

Current limitations that this tranche must address:

- only one dominant normalization ratio
- no lane-specific normalization notes
- no explicit local-cost versus external-cost boundary
- no stronger invalid-comparison interpretation carried into the normalized view
- no clear distinction between descriptive and claim-bearing comparative fields

## 7. Execution epics

### EPIC A — Semantics and policy tightening

Objective:

- define what compute-normalized comparison means in DARWIN Phase-2

Includes:

- normalization vocabulary
- cost-accounting vocabulary
- interpretation policy
- explicit non-claimable surfaces

### EPIC B — Stronger normalized artifact surface

Objective:

- build a stricter normalized view over the current baseline/search/archive outputs

Includes:

- upgraded compute-normalized JSON artifact
- richer lane rows
- clearer cost fields
- derived summary fields for comparative review

### EPIC C — Comparative scorecard and memo integration

Objective:

- make the normalized surface usable by scorecards and internal comparative review

Includes:

- stronger scorecard refs
- comparative memo updates
- clearer qualitative interpretation over normalized values

### EPIC D — Proving, audit, and tranche gate

Objective:

- validate that the stronger normalized surface improves interpretability without changing runtime truth

Includes:

- proving-lane checks
- audit-lane checks
- tranche decision about whether broader transfer/lineage work now has enough semantic footing

## 8. Exact work packages

### WP-01 — Write the normalization policy

Deliverables:

- a DARWIN contract/policy doc for stronger compute-normalized semantics
- definitions for:
  - runtime-normalized score
  - local-cost-normalized score
  - eligible cost fields
  - prohibited inference patterns
  - lane-specific interpretation notes

Success test:

- the policy makes it clear what the normalized view can and cannot support

### WP-02 — Upgrade the compute-normalized artifact

Deliverables:

- successor to the current companion view
- richer lane rows that distinguish:
  - raw score
  - runtime normalization
  - local-cost normalization
  - baseline versus active candidate comparison
  - interpretation flags

Success test:

- the artifact is more informative than `compute_normalized_view_v1.json` without changing any runtime-producing path

### WP-03 — Tighten cost-accounting semantics

Deliverables:

- explicit accounting fields for:
  - evaluator runtime
  - local run cost proxies
  - unavailable or intentionally absent external billing fields
- policy notes on what is exact, estimated, or unavailable

Success test:

- the scorecard can distinguish exact versus approximate versus unavailable cost semantics

### WP-04 — Integrate normalized view into comparative surfaces

Deliverables:

- updated comparative scorecard references
- updated comparative memo path
- clearer normalized summaries for proving lanes

Success test:

- a reviewer can inspect `lane.harness` and `lane.repo_swe` and understand comparative differences without reading raw underlying artifacts first

### WP-05 — Proving and audit checks

Deliverables:

- proving validation on `lane.harness` and `lane.repo_swe`
- audit-only confirmation on `lane.scheduling` and `lane.atp`
- a short tranche review stating whether broader transfer/lineage work can now proceed

Success test:

- stronger normalized surfaces are validated and clearly bounded before the next tranche is selected

## 9. ADR candidates for this tranche

### ADR-CN-01 — Compute-normalized semantics

Answer:

- which normalized fields are canonical for this tranche
- which are descriptive only
- which are explicitly non-claim-bearing

### ADR-CN-02 — Cost-accounting classification

Answer:

- exact versus estimated versus unavailable fields
- what counts as acceptable local accounting
- what is prohibited from being presented as stronger than it is

### ADR-CN-03 — Scorecard integration boundary

Answer:

- what belongs in the normalized artifact
- what belongs in scorecards and memos only
- what remains derived-only and non-canonical

## 10. Defer list

This tranche explicitly defers:

- broader transfer families
- richer multi-tranche lineage policy
- external-safe evidence packet work
- runtime consumption of `EvaluatorPack` or `EvolutionLedger`
- new lane rollout

## 11. Hard gate

This tranche is complete only if:

- stronger normalization semantics are written and linked
- the normalized artifact is upgraded and validated
- cost-accounting classification is explicit
- `lane.harness` and `lane.repo_swe` are convincingly explained by the new view
- `lane.scheduling` and `lane.atp` pass audit-only confirmation
- a review note states whether the later transfer/lineage tranche should now proceed

## 12. Read

This tranche is intentionally narrower than the broader Phase-2 ambitions.

That is correct.

The point is to make comparative reasoning stronger before we expand transfer families or evidence packaging.
