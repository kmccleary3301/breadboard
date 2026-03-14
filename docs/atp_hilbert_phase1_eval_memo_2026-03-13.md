# ATP Hilbert Phase 1 Evaluation Memo — 2026-03-13

## Purpose

This memo summarizes the final Phase 1 state of the maintained-Hilbert comparison program after locking the canonical tranche baselines, building the aggregate scoreboard, backfilling BreadBoard spend estimates, separating repaired versus no-repair arms, and completing the replay audit.

Reference artifacts:

- canonical baseline lock: `artifacts/benchmarks/hilbert_comparison_packs_v2/canonical_baseline_index_v1.json`
- aggregate scoreboard: `artifacts/benchmarks/hilbert_comparison_packs_v2/scoreboard_v1.json`
- aggregate scoreboard markdown: `artifacts/benchmarks/hilbert_comparison_packs_v2/scoreboard_v1.md`

## Headline Result

Across the current headline pack set (`canonical_primary`, `canonical_rollup`, and `boundary_stress`):

- headline packs: `12`
- tranche families covered:
  - bounded arithmetic / modular arithmetic
  - algebraic equality / linear-equivalence
  - gcd / divisibility / residue-number-theory
  - mixed induction plus number theory
  - explicit stress / boundary packs
- maintained comparator:
  - `Rose-STL-Lab/ml-hilbert`
- Phase 1 gate state:
  - complete

Program-level interpretation:

- BreadBoard is ahead on most canonical headline packs.
- BreadBoard is not behind on any canonical headline pack.
- The only headline ties are the explicit boundary-stress packs where both systems fail under bounded caps.
- Replay audit does not change the program-level interpretation: BreadBoard stayed stable on the audited easy and medium packs, while Hilbert improved by one task on each replay but still remained behind.

## Strongest BreadBoard-Favorable Evidence

### Pack B core-noimo

- `6/6` for BreadBoard versus `2/6` for maintained Hilbert
- BreadBoard-only wins:
  - `mathd_algebra_156`
  - `mathd_numbertheory_530`
  - `numbertheory_2dvd4expn`
  - `numbertheory_exk2powkeqapb2mulbpa2_aeq1`

### Pack D mixed roll-up

- `6/6` for BreadBoard versus `1/6` for maintained Hilbert
- This is important because it combines:
  - induction
  - gcd-style number theory
  - divisor filtering
  - one algebra cleanup theorem

### Packs G through L

These show that BreadBoard remains ahead after the earlier high-yield tranche families:

- Pack G: `6/6` versus `4/6`
- Pack H: `6/6` versus `3/6`
- Pack I: `6/6` versus `3/6`
- Pack J: `6/6` versus `4/6`
- Pack K: `5/5` versus `3/5`
- Pack L: `6/6` versus `3/6`

The significance here is not one isolated win. The same comparison lane kept favoring BreadBoard across several bounded theorem families after the Pack B and Pack D gains.

### Pack M boundary tranche

- Pack M: `0/4` versus `0/4`

This is useful because it locates a harder region where the current BreadBoard advantage disappears instead of widening. That is a healthier Phase 1 ending point than an unbroken sequence of easy wins.

## Supporting Evidence and Caveats

### Focused repair matters

Several canonical lanes use focused BreadBoard repair passes to close theorem-local misses. That is valid evidence, but it must not be confused with a pure one-shot harness result.

Current program state:

- some packs are pure baseline wins
- some packs become canonical only after focused theorem-local reruns
- baseline-versus-repaired accounting is now explicit in the arm audit and no-repair slice artifacts

### The comparison is against the maintained Hilbert fork

The active external comparator is the maintained fork:

- `Rose-STL-Lab/ml-hilbert`

This is not the stale Apple repository. That matters for the strength of the comparison claim.

### Validity filtering is a real part of the program

Some extracted theorems were provably invalid under current Lean/Mathlib semantics and were excluded. That is not benchmark cherry-picking; it is necessary hygiene.

Observed invalidity classes include:

- `Nat` subtraction truncation
- `Real.logb` semantics on negative inputs
- stale Lean 3 namespace and lambda syntax
- theorem heads with missing binders or free variables

This is why the tranche-selection policy and invalid-extract ledger are required next.

### BreadBoard spend remains estimated, not provider-native

Maintained-Hilbert spend is tracked exactly from proof-stat telemetry for the canonical packs. BreadBoard spend is now backfilled from runner usage ledgers and static OpenRouter pricing, but it is still an estimate rather than a provider-native billing stream.

That limitation should be carried into Phase 2 interpretation, but it no longer blocks Phase 1 closure.

## What the current evidence supports

The current evidence supports the following claims:

- the BreadBoard ATP comparison lane is operationally stable
- the maintained-Hilbert comparator lane is operationally stable
- BreadBoard is consistently competitive on current valid bounded miniF2F tranches
- on the current headline tranche set, BreadBoard is usually ahead and never behind
- the program now has enough breadth, replay evidence, and governance to support a defensible Phase 1 comparison claim

The current evidence does **not** yet support:

- a blanket claim over all miniF2F
- a blanket claim over harder IMO/Putnam-style ATP tasks
- a clean provider-native efficiency comparison, because BreadBoard spend is estimated rather than billed directly
- a claim that the current bounded settings define the absolute capability frontier for either system

## Phase 2 entrypoint

Phase 1 is complete. The next phase should focus on breadth expansion beyond Pack M and on less curated evaluation modes.

Current queued entrypoint:

- `breadboard_repo_atp_followup_20260310-jbk` — define the next ATP tranche beyond Pack M

## Bottom Line

The program is no longer proving that BreadBoard can occasionally beat Hilbert on a hand-picked slice.

It is now showing a repeatable pattern:

- valid tranche construction
- bounded and reproducible comparator runs
- canonical promotion rules
- multi-pack evidence where BreadBoard is usually ahead of the maintained Hilbert fork

Phase 1 is closed. Phase 2 is not infrastructure work. It is broader benchmark expansion, harder boundary exploration, and less hand-held evaluation.
