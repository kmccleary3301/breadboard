# ATP Hilbert Tranche Selection Policy v1

## Purpose

This policy fixes how new ATP comparison tranches are selected, filtered, executed, and promoted to canonical evidence in the maintained-Hilbert comparison lane.

The goal is to prevent:

- silent drift in benchmark validity
- spend-heavy but low-signal tranche choices
- conflating baseline runs with theorem-local repair passes
- over-counting stale or superseded report variants

## Scope

This policy applies to all tranche work after Packs B through J in the direct-formal `bb_hilbert_like` versus `hilbert_roselab` program.

It covers:

- tranche construction
- exclusion criteria
- execution protocol
- canonical promotion
- focused repair usage
- stress/boundary handling

It does not replace the lower-level cross-system manifest and validation contracts in `docs/contracts/benchmarks/README.md`.

## Tranche Classes

### Canonical primary tranche

Use for the main comparison program.

Requirements:

- valid extracted statements only
- bounded expected spend
- family-coherent theorem set
- paired BreadBoard/Hilbert report
- clean validation report
- one status note naming the canonical artifacts

Examples:

- `pack_b_core_noimo_minif2f_v1`
- `pack_e_algebra_core_minif2f_v1`
- `pack_f_discrete_arithmetic_mix_minif2f_v1`
- `pack_g_arithmetic_sanity_minif2f_v1`
- `pack_h_modular_closedform_minif2f_v1`
- `pack_i_divisors_modmix_minif2f_v1`
- `pack_j_residue_gcd_mix_minif2f_v1`

### Supporting tranche

Use when the tranche exists to clarify or saturate a primary tranche rather than replace the program’s main lane.

Allowed subtypes:

- filtered support lane
- focused follow-up lane
- split follow-up lane

Examples:

- `pack_b_medium_noimo530_minif2f_v1`
- `pack_d_induction_core_minif2f_v1`
- `pack_d_numbertheory_core_minif2f_v1`
- `pack_e_algebra_focus_minif2f_v1`

Supporting tranches may feed canonical roll-ups, but they do not automatically replace the primary pack family unless the status note says so explicitly.

### Boundary or stress tranche

Use when the goal is to probe a difficulty frontier or bound spend/risk on a hard theorem.

Requirements:

- explicit spend approval or explicit bounded cap
- explicit note that the tranche is not a calibration lane
- kept separate from aggregate calibration claims

Example:

- `pack_c_imo1977_p6_stress_minif2f_v1`

## Validity Rules

Every new tranche must be screened for statement validity before execution.

Reject or exclude a task if any of the following are true:

- malformed theorem head
- missing binders or free variables
- extracted statement is provably unsound under current Lean/Mathlib semantics
- namespace drift or legacy syntax prevents fair execution
- theorem semantics change materially under current canonicalization rules

Known invalid classes already observed:

- `Nat` subtraction truncation changing arithmetic meaning
- `Real.logb` semantics making negative-input theorems false
- stale Lean 3 namespace forms like `nat.*`, `finset.*`, lowercase `complex.*`
- lambda syntax drift like `λ x, ...` that breaks maintained-Hilbert parsing
- standalone `finset` identifiers that must be canonicalized to `Finset`

Every exclusion must carry:

- task id
- explicit reason
- counterexample or semantic explanation when available
- location in the builder or exclusion ledger

## Tranche Size and Cost Rules

Default tranche size:

- primary tranche: `5-6` tasks
- focused tranche: `1-4` tasks
- stress tranche: `1` task unless explicitly approved otherwise

Default cost posture:

- prefer bounded, sequential maintained-Hilbert runs
- do not start with spend-heavy IMO/Putnam-style tasks in a calibration tranche
- isolate known expensive tasks in stress lanes
- stop using a theorem as a calibration task once it is proven to be open-ended under current caps

Selection rules:

- at least one tranche per new family should be cheap and fast enough to rerun
- at least one periodic tranche should sit near the current difficulty boundary
- do not keep farming already-saturated easy families when the next goal is broader evidence

## Family Diversity Rules

Do not let tranche selection collapse into one narrow theorem family.

Across the active program, maintain breadth across families such as:

- algebra
- modular arithmetic
- gcd/lcm and divisors
- discrete contest arithmetic
- induction
- mixed number theory

When one family is saturated:

- stop expanding that family unless it is needed for replay audit or no-repair comparison
- move to a new valid family rather than accumulating more trivial wins

## Execution Protocol

Every tranche must follow this order:

1. build pack with canonicalization and exclusion logic
2. run BreadBoard bounded baseline
3. run maintained Hilbert bounded baseline
4. convert Hilbert output
5. validate paired matrix
6. build paired report
7. write status note

Only after that may focused repair begin.

## Baseline vs Repair Rules

The program must distinguish two BreadBoard arms:

- baseline arm: first bounded full-pack BreadBoard run
- repaired arm: focused theorem-local reruns merged back into the canonical rowset when justified

Rules:

- never overwrite or hide the baseline result
- every repaired canonical rowset must name the focused reruns that changed it
- if a full-pack rerun regresses previously solved tasks, keep the merged rowset and mark the regressing rerun non-canonical
- status notes must say whether the current canonical pack is:
  - pure baseline
  - focused repaired
  - split-rollup derived

## Promotion to Canonical Evidence

A tranche becomes canonical only if all of the following hold:

- report file is fixed
- validation file is fixed and `ok=true`
- status note names the canonical paths
- invalid tasks are excluded or explicitly justified
- spend note exists for maintained Hilbert
- any focused repair inputs are documented

The canonical baseline index in:

- `artifacts/benchmarks/hilbert_comparison_packs_v2/canonical_baseline_index_v1.json`

is the source of truth for currently locked evidence.

## Replay and Stability Rules

At least one easy tranche and one medium tranche must be replayed periodically.

Replay goal:

- verify that canonical outcomes are stable under the same caps and canonicalized statements

If a replay drifts:

- do not silently replace the canonical result
- write a drift note
- explain whether the cause is model nondeterminism, canonicalization drift, or runner/prover change

## Pack Construction Checklist

Before approving a new Pack K+ tranche, verify:

- tasks come from a coherent theorem family
- no known-invalid tasks remain
- no cost-skew theorem dominates the slice without explicit stress labeling
- expected tranche size is bounded
- BreadBoard and maintained-Hilbert can both consume the statements after canonicalization
- the pack adds new evidence rather than duplicating a saturated family

## Current Stopping Condition for Phase 1

Phase 1 is complete only when all of the following are true:

- canonical pack family is locked
- aggregate scoreboard exists
- aggregate evaluation memo exists
- BreadBoard spend ledger exists and is backfilled across canonical packs
- at least one no-repair slice exists
- at least one boundary tranche exists
- at least one replay audit exists
- at least ten canonical valid tranches or equivalent canonical family slices exist

Until then, tranche expansion must prioritize evidence breadth over repeated work inside saturated packs.
