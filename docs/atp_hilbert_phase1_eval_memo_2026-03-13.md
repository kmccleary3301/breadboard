# ATP Hilbert Phase 1 Evaluation Memo — 2026-03-13

## Purpose

This memo summarizes the current state of the maintained-Hilbert comparison program after locking the canonical tranche baselines and building the first aggregate scoreboard.

Reference artifacts:

- canonical baseline lock: `artifacts/benchmarks/hilbert_comparison_packs_v2/canonical_baseline_index_v1.json`
- aggregate scoreboard: `artifacts/benchmarks/hilbert_comparison_packs_v2/scoreboard_v1.json`
- aggregate scoreboard markdown: `artifacts/benchmarks/hilbert_comparison_packs_v2/scoreboard_v1.md`

## Headline Result

Across the current headline pack set (`canonical_primary`, `canonical_rollup`, and `boundary_stress` only):

- primary packs: `9`
- headline tasks: `48`
- BreadBoard solves: `47`
- maintained Hilbert solves: `23`
- BreadBoard-only task wins: `24`
- Hilbert-only task wins: `0`
- packs with BreadBoard ahead: `8`
- tied packs: `1`
- packs with Hilbert ahead: `0`
- maintained-Hilbert exact spend across headline packs: `~$3.963843`

The only tied headline pack is the Pack C stress lane, where both systems remain `0/1` on `imo_1977_p6`.

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

### Packs G through J

These show that BreadBoard remains ahead after the earlier high-yield tranche families:

- Pack G: `6/6` versus `4/6`
- Pack H: `6/6` versus `3/6`
- Pack I: `6/6` versus `3/6`
- Pack J: `6/6` versus `4/6`

The significance here is not one isolated win. The same comparison lane kept favoring BreadBoard across several bounded theorem families after the Pack B and Pack D gains.

## Supporting Evidence and Caveats

### Focused repair matters

Several canonical lanes use focused BreadBoard repair passes to close theorem-local misses. That is valid evidence, but it must not be confused with a pure one-shot harness result.

Current program state:

- some packs are pure baseline wins
- some packs become canonical only after focused theorem-local reruns
- the baseline versus repaired distinction still needs first-class program-level accounting

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

### BreadBoard spend is still under-instrumented

Maintained-Hilbert spend is now tracked exactly for headline packs. BreadBoard spend is not yet available as a clean provider-side ledger in the direct formal runner path.

That is the biggest remaining measurement gap in the current evaluation program.

## What the current evidence supports

The current evidence supports the following claims:

- the BreadBoard ATP comparison lane is operationally stable
- the maintained-Hilbert comparator lane is operationally stable
- BreadBoard is consistently competitive on current valid bounded miniF2F tranches
- on the current headline tranche set, BreadBoard is usually ahead and never behind
- the main remaining weakness is not theorem validity or run hygiene; it is program-level measurement completeness and breadth expansion

The current evidence does **not** yet support:

- a blanket claim over all miniF2F
- a blanket claim over harder IMO/Putnam-style ATP tasks
- a clean efficiency comparison, because BreadBoard spend is still missing
- a pure no-repair comparison claim, because repaired and unrepaired runs are not yet separated at the scoreboard level

## Immediate Gaps

The current high-value gaps are:

- automated aggregate rollup generation
- BreadBoard spend ledger and backfill
- explicit baseline-versus-repaired accounting
- invalid-extract ledger
- replay audit on canonical packs
- one no-repair tranche
- next tranche expansion beyond Pack J

## Recommended Next Actions

In order:

1. automate the rollup from the canonical baseline index
2. add BreadBoard spend accounting to the direct formal runner
3. backfill BreadBoard spend on canonical packs
4. define and run Pack K under the tranche-selection policy
5. add one no-repair tranche to separate harness-level signal from theorem-local repair signal
6. run one replay audit on an easy and a medium canonical pack

## Bottom Line

The program is no longer proving that BreadBoard can occasionally beat Hilbert on a hand-picked slice.

It is now showing a repeatable pattern:

- valid tranche construction
- bounded and reproducible comparator runs
- canonical promotion rules
- multi-pack evidence where BreadBoard is usually ahead of the maintained Hilbert fork

The next phase is not infrastructure work. It is measurement completion and breadth expansion.
