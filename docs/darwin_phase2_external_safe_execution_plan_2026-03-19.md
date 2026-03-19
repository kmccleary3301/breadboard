# DARWIN Phase-2 External-Safe Evidence Tranche Execution Plan

Date: 2026-03-19
Status: active planning tranche
Primary issue: `breadboard_repo_darwin_phase1_20260313-ovi`
Scope: additive-first external-safe evidence packaging on top of completed Phase-2 comparative and transfer/lineage tranches

## 1. Purpose

This tranche converts the current DARWIN internal evidence program into a bounded, reviewer-legible, external-safe package.

The goal is not to make a public launch claim. The goal is to make the current DARWIN evidence surface precise enough that an external technical reviewer could inspect it without silently inheriting internal-only assumptions.

## 2. Why this tranche is next

The compute-normalized tranche and transfer/lineage tranche are both complete for their bounded purposes.

What remains weak is not internal structure. What remains weak is packaging discipline:

- which claims are safe beyond internal use
- which artifacts are canonical for reviewer-facing reference
- which replay and contamination caveats must travel with the evidence
- which comparative statements must remain non-claims

That makes external-safe evidence packaging the correct next tranche.

## 3. Tranche mission

> package the current DARWIN evidence into an external-safe, replay-conditioned, reviewer-legible bundle without widening runtime truth or comparative ambition.

## 4. Boundary

### Must do

- define external-safe evidence policy
- define the canonical external-safe artifact set
- build an external-safe memo, claim subset, packet, reviewer summary, and reproducibility note
- preserve invalid-comparison and replay caveats in the reviewer-facing layer
- keep cost-accounting caution language explicit and uniform

### Must not do

- no new runtime-truth primitive
- no runtime consumption of tranche-1 compiled artifacts
- no broader transfer-family expansion
- no new lane rollout
- no async or large-scale inference expansion
- no superiority claims
- no publication-grade or frontier-grade comparative claims

## 5. Lane posture

Primary reviewer-facing proving lanes:

- `lane.repo_swe`
- `lane.scheduling`

Supporting bounded lanes:

- `lane.harness`
- `lane.research`

Audit-only:

- `lane.atp`

Not primary in this tranche:

- `lane.systems`

## 6. Execution epics

### EPIC A — External-safe policy and artifact freeze

Objective:

- define what is claimable, what is prohibited, and which artifacts are canonical for the tranche

### EPIC B — External-safe packet and claim subset

Objective:

- derive a bounded claim set and evidence packet from existing DARWIN artifacts without changing the underlying runtime story

### EPIC C — Reviewer summary and reproducibility layer

Objective:

- make the current evidence legible and rebuildable for a skeptical technical reviewer

### EPIC D — Tranche review and closeout recommendation

Objective:

- decide whether the current scoped DARWIN program is now closeout-ready or still missing one bounded refinement

## 7. Exact work packages

### WP-01 — Write the external-safe evidence policy

Deliverables:

- policy doc under `docs/contracts/darwin/`
- allowed vs prohibited claim classes
- required caution labels and reviewer warnings

Success test:

- the policy makes it impossible to confuse internal operational claims with external-safe comparative claims

### WP-02 — Freeze the canonical external-safe artifact set

Deliverables:

- canonical artifact index doc
- explicit included vs internal-only artifact lists

Success test:

- the memo and packet can point to one stable artifact set rather than ad hoc references

### WP-03 — Build the external-safe claim subset and packet

Deliverables:

- derived external-safe claim subset
- derived external-safe packet
- invalidity summary if needed for legibility

Success test:

- prohibited claims are filtered out or downgraded and the packet stays additive-only

### WP-04 — Build reviewer memo and reproducibility note

Deliverables:

- external-safe evidence memo
- shorter reviewer summary
- bounded reproduction note

Success test:

- an external technical reviewer can reconstruct the scoped story without reading every internal tranche document

### WP-05 — Tranche review and closeout recommendation

Deliverables:

- status doc
- tranche review
- explicit note on whether only final closeout work remains

Success test:

- the next move is clearly either final current-program closeout or one last bounded refinement
