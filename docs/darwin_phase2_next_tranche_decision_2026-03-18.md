# DARWIN Phase-2 Next Tranche Decision

Date: 2026-03-18
Status: selected
Selected tranche: compute-normalized comparative layer
Primary issue: `breadboard_repo_darwin_phase1_20260313-8xc`

## Decision

The next scoped Phase-2 tranche after tranche 1 is:

- stronger compute-normalized comparative layer

The next tranche does **not** select:

- broader transfer families and lineage policy
- external-safe evidence packet expansion

## Why this tranche is next

Tranche 1 proved that DARWIN can emit compiled execution, policy, evaluator, and minimal decision/lineage surfaces in additive-only form.

That makes the next highest-value move:

- improve comparative interpretation over already-emitted artifacts
- tighten normalization semantics and cost accounting
- stay additive-only and avoid runtime-truth expansion

This choice matches the current tranche-1 hard-gate review:

- runtime dependency expansion remains blocked
- archive remains derived-only
- ledger remains reconstruction-first

Given those constraints, compute-normalized comparison is the highest-yield next step because it:

- uses Phase-1 and tranche-1 artifacts as they already exist
- raises decision quality without creating new runtime truth
- creates a stronger review surface for later transfer and lineage decisions
- keeps blast radius below the broader lineage-policy path

## Why the other candidate tranches are deferred

### `breadboard_repo_darwin_phase1_20260313-xrz` — broader transfer families and lineage policy

This is the right tranche **after** stronger comparative normalization, not before it.

Reasons:

- current `EvolutionLedger` is reconstruction-first, not dual-write
- current transfer and promotion semantics are coherent enough for audit, but not yet strong enough for broader family expansion
- broader transfer work without tighter normalization would increase interpretation ambiguity
- that path creates more pressure toward runtime or semi-runtime truth than the compute-normalized tranche does

### `breadboard_repo_darwin_phase1_20260313-ovi` — external-safe evidence packet

This should remain downstream of stronger comparative semantics.

Reasons:

- external-safe packaging should not outpace internal comparative rigor
- current evidence surfaces are good enough for internal Phase-2 work
- improving narrative packaging before normalization and comparison quality would optimize the wrong layer

## Scope boundary for the selected tranche

### Must do

- define stronger compute-normalization semantics over existing DARWIN artifacts
- improve cost-accounting clarity and interpretation rules
- produce clearer comparative scorecard surfaces
- keep ATP in an audit/secondary role rather than making it the proving center
- keep the work additive-only unless a later review explicitly authorizes more

### Must not do

- no runtime consumption of tranche-1 emitted artifacts
- no new BreadBoard kernel-truth primitive
- no new lane expansion
- no broader transfer-family rollout
- no external/public-safe evidence packaging tranche
- no async dependency

## Expected proving shape

The selected tranche should primarily prove on:

- `lane.harness`
- `lane.repo_swe`

Secondary confirmation or audit only:

- `lane.scheduling`
- `lane.atp`

Not primary proving lanes for this tranche:

- `lane.systems`
- `lane.research`

## What this tranche should produce

The next tranche should end with:

- a stricter compute-normalized view over current scorecards
- clearer cost-accounting fields and interpretation policy
- a DARWIN comparative memo that uses the stronger normalized surfaces
- an explicit decision about whether the later transfer/lineage tranche now has enough semantic footing

## Read

The correct next move is not to keep extending tranche 1 by inertia.

The correct next move is:

- freeze tranche-1 additive surfaces as coherent
- start a new scoped additive tranche on comparative normalization
- defer broader transfer families and external-safe evidence until that tranche lands

Current execution-plan reference:

- `docs/darwin_phase2_compute_normalized_execution_plan_2026-03-18.md`
