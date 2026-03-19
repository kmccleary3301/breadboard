# DARWIN Phase-2 Transfer and Lineage Tranche Execution Plan

Date: 2026-03-18
Status: selected planning tranche
Primary issue: `breadboard_repo_darwin_phase1_20260313-xrz`
Scope: additive-first transfer-family and lineage-policy expansion after the compute-normalized tranche

## 1. Purpose

This document defines the next scoped Phase-2 tranche after the compute-normalized tranche was judged complete for its current scope.

The purpose of this tranche is:

- broaden transfer work beyond the first bounded protocol
- tighten lineage, promotion, rollback, and archive semantics
- keep all new work additive-first until a later review says otherwise

This tranche is explicitly downstream of:

- `docs/darwin_phase2_tranche1_execution_plan_2026-03-14.md`
- `docs/darwin_phase2_compute_normalized_execution_plan_2026-03-18.md`
- `docs/darwin_phase2_compute_normalized_tranche_decision_2026-03-18.md`
- `docs/contracts/darwin/DARWIN_MODEL_ROUTING_POLICY_V0.md`

## 2. Why this tranche is next

The compute-normalized tranche was the missing comparative-clarity step. That step is now complete enough for the next move.

What remains weak is not basic interpretability. It is the scope and policy discipline of transfer and lineage itself.

The current system still has:

- only the first bounded transfer protocol
- limited family coverage
- reconstruction-first lineage semantics
- insufficiently explicit rollback / deprecation / supersession policy for broader multi-tranche use

That makes `xrz` the correct next active tranche.

## 3. Tranche mission

The mission of this tranche is:

> broaden DARWIN transfer families and tighten lineage policy enough that multi-tranche transfer and promotion decisions become more systematic, while keeping runtime truth unchanged.

## 4. Boundary

### Must do

- define broader but still bounded transfer-family coverage
- define richer multi-tranche lineage, promotion, rollback, and supersession policy
- improve derived archive and decision views so they are easier to reason about
- prove the work on a narrow lane set
- use DARWIN-local model routing defaults if real inference enters this tranche

### Must not do

- no runtime consumption of tranche-1 compiled artifacts
- no new BreadBoard kernel-truth primitive
- no async dependency
- no new lane expansion
- no external-safe evidence packet work
- no public or superiority-style transfer claims
- no repo-wide provider-default retuning as part of this tranche

## 5. Primary proving shape

Primary proving lanes:

- `lane.repo_swe`
- `lane.scheduling`

Secondary proving or transfer-source/target roles:

- `lane.harness`
- `lane.research`

Audit-only:

- `lane.atp`

Not primary in this tranche:

- `lane.systems`

## 6. Current weakness in the existing transfer/lineage layer

Current strengths:

- first bounded transfer protocol exists
- minimal `EvolutionLedger` exists
- promotion history exists
- compute-normalized comparative surfaces now exist

Current weaknesses this tranche must address:

- transfer families are too narrow
- lineage semantics are still more reconstructed than policy-driven
- rollback and supersession semantics are not yet explicit enough for broader multi-tranche use
- archive views are still coherent, but not yet rich enough for the next policy step

## 7. Execution epics

### EPIC A — Transfer-family policy expansion

Objective:

- define which transfer families are in-scope next and under what validity rules

Includes:

- source/target lane matrix
- transfer-family types
- explicit validity and replay expectations
- non-claimable transfer outcomes

### EPIC B — Lineage and decision-policy tightening

Objective:

- strengthen promotion, rollback, supersession, and deprecation policy

Includes:

- multi-tranche lineage rules
- decision-state vocabulary
- rollback target expectations
- archive semantics as derived but easier-to-read views

### EPIC C — Derived artifact and review upgrades

Objective:

- make transfer and lineage decisions easier to audit without moving runtime truth

Includes:

- stronger derived transfer-family views
- stronger derived lineage / supersession views
- review artifacts for proving lanes

### EPIC D — Proving, audit, and tranche gate

Objective:

- validate that broader transfer-family and lineage policy are coherent before any later runtime or evidence expansion is considered

Includes:

- proving-lane checks
- audit lane checks
- tranche review and explicit next-step decision

## 8. Exact work packages

### WP-01 — Write the transfer-family policy

Deliverables:

- a DARWIN policy doc defining the next bounded transfer-family set
- explicit rules for:
  - valid transfer
  - invalid transfer
  - replay-required transfer
  - descriptive-only transfer result

Success test:

- the policy makes clear which new transfer families are allowed and what evidence each requires

### WP-02 — Write the lineage / rollback / supersession policy

Deliverables:

- a DARWIN policy doc for:
  - promotion state
  - rollback state
  - supersession state
  - deprecation state
  - multi-tranche lineage expectations

Success test:

- the policy makes current derived lineage outputs easier to interpret and exposes the missing cases directly

### WP-03 — Upgrade derived lineage and transfer artifacts

Deliverables:

- richer derived transfer-family view
- richer derived lineage or promotion-history companion view
- explicit rollback / supersession markers where applicable

Success test:

- the derived views explain selected known cases more clearly without changing runtime-producing paths

### WP-04 — Prove on selected lane pairs

Deliverables:

- proving on `lane.repo_swe` and `lane.scheduling`
- bounded transfer-family checks involving `lane.harness` and `lane.research`
- audit-only confirmation on `lane.atp`

Success test:

- the tranche produces clearer transfer and lineage reasoning on real selected cases without over-broadening the lane set

### WP-05 — Tranche review

Deliverables:

- status note
- tranche review note
- explicit decision on whether external-safe evidence work is now the next best tranche

Success test:

- the next move is clear and justified

## 9. ADR candidates for this tranche

### ADR-TL-01 — Transfer-family boundary

Answer:

- which transfer families are in scope
- which are explicitly deferred
- what validity and replay conditions apply

### ADR-TL-02 — Lineage decision-state vocabulary

Answer:

- promotion
- rollback
- supersession
- deprecation
- retained baseline

### ADR-TL-03 — Archive and lineage truth boundary

Answer:

- what remains derived-only
- what becomes more explicit in the derived views
- what still must not be treated as runtime authority

## 10. Defer list

This tranche explicitly defers:

- external-safe evidence packet work
- runtime consumption of `EvolutionLedger`
- new BreadBoard kernel-truth objects
- new live lanes
- async or distributed expansion

## 11. Hard gate

This tranche is complete only if:

- transfer-family policy is explicit
- lineage / rollback / supersession policy is explicit
- derived views are improved on selected proving cases
- proving and audit lanes are reviewed
- a clear next-tranche decision is recorded

## 12. Read

This tranche should remain narrower than it may first appear.

The goal is not to “solve transfer” in one pass.

The goal is to make transfer and lineage policy disciplined enough that later evidence work has a sound substrate.
