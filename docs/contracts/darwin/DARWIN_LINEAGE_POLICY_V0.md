# DARWIN Lineage Policy V0

Date: 2026-03-19
Status: active internal tranche policy
Scope: Phase-2 transfer/lineage tranche

## Purpose

This document defines the bounded lineage, rollback, supersession, and deprecation semantics used by the current transfer/lineage tranche.

The goal is to make multi-tranche candidate history easier to reason about without turning the lineage layer into runtime truth.

## Decision states

### `retained_baseline`

Meaning:

- baseline candidate remains active after comparison

### `promoted`

Meaning:

- candidate becomes the active promoted candidate for the current lane state

### `rejected`

Meaning:

- candidate remains recorded but does not become active

### `superseded`

Meaning:

- candidate was once active or eligible but has been replaced by a later promoted candidate

### `deprecated`

Meaning:

- candidate or component remains in history but should not be reused as an active future reference without explicit review

### `rollback_candidate`

Meaning:

- candidate is the immediate rollback target if the active promoted candidate later fails review

### `rolled_back`

Meaning:

- previously active candidate has been replaced by an earlier rollback target

## Multi-tranche lineage rule

Each later tranche should be able to answer:

- what was the active candidate entering the tranche
- what earlier candidate is the rollback target
- which earlier candidates are superseded
- whether any candidate is deprecated for future reuse

This does not require runtime dual-write in the current tranche. It requires derived lineage views to expose the state explicitly.

## Rollback policy

Rollback may be considered when:

- a promoted candidate fails replay
- a later audit invalidates a previously valid comparison
- a later tranche identifies a stronger policy reason to return to a prior candidate

Rollback should record:

- active candidate being displaced
- rollback target candidate
- triggering reason
- evidence refs

## Supersession policy

Use `superseded` when:

- a candidate was replaced by a later promoted candidate
- the older candidate is still historically valid
- the older candidate remains useful for lineage reasoning or rollback context

## Deprecation policy

Use `deprecated` when:

- a candidate or component should remain visible in history
- but should not be reused automatically in future transfer or promotion work

Examples:

- invalid after later policy review
- no longer acceptable under tightened transfer rules

## Archive boundary

Archive views remain derived-only.

They may expose:

- current promotion state
- rollback target hints
- supersession chain hints

But decision truth remains in typed decision and lineage policy, not in archive rows alone.

## Current proving posture

Primary lineage proving lane:

- `lane.scheduling`

Primary transfer-sensitive lineage lane:

- `lane.repo_swe`

Secondary source/target lineage context:

- `lane.harness`
- `lane.research`

Audit-only:

- `lane.atp`

## Boundary

- no new BreadBoard kernel-truth primitive
- no runtime consumption authorized by this policy alone
- no claim that lineage policy is yet publication-grade
