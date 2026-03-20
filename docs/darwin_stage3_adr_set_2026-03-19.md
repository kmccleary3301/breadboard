# DARWIN Stage-3 ADR Set

Date: 2026-03-19
Status: proposed ADR set for Stage 3
References:
- `docs/darwin_stage3_doctrine_2026-03-19.md`
- `docs/darwin_stage3_execution_plan_2026-03-19.md`
- `docs/darwin_stage3_tranche1_slice_2026-03-19.md`
- `docs_tmp/DARWIN/BB_DARWIN_PHASE_3_PLANNER_RESPONSE.md`

## Purpose

This ADR set translates the Stage-3 planner response into the smallest implementation-grade decision set needed to begin Stage 3.

It does not authorize broad coding by itself. It defines the decisions that must govern the first implementation tranche.

## Global Stage-3 rules

1. Current-program closure on `main` remains frozen.
2. Stage 3 is a new program, not a continuation of Phase-2 closeout.
3. Runtime truth remains singular and BreadBoard-owned.
4. Stage-3 primitives should increase search power or reuse clarity, not packaging density.
5. Repo_SWE and Systems are the long-term primary proving lanes.
6. Harness remains the low-cost control lane.
7. ATP remains audit-only.
8. Research remains deferred from centrality early in Stage 3.

---

## ADR-01 — Stage-3 doctrine and lane taxonomy

### Status

Proposed.

### Problem

The current DARWIN program is fully closed. Without a new doctrine, Stage 3 will drift into local continuation rather than deliberate program design.

### Decision

Adopt a new Stage-3 doctrine with:

- a clean program reset
- a fixed early lane taxonomy
- explicit non-goals
- explicit model-routing doctrine
- explicit blast-radius rules

### Lane taxonomy

- foundation: `lane.harness`, `lane.repo_swe`
- real-search primary: `lane.repo_swe`, `lane.systems`
- secondary confirmation: `lane.scheduling`
- audit: `lane.atp`
- deferred centrality: `lane.research`

### Non-goals

- not a new current-program closeout
- not an ATP roadmap reset
- not a lane-expansion program
- not async-first search

### Exit criteria

- doctrine is written and linked
- lane roles are fixed for the first half of Stage 3
- early Stage-3 work can be evaluated against this boundary

---

## ADR-02 — `OptimizationSubstrateV1` ownership and boundary

### Status

Proposed.

### Problem

The repo already contains a stronger optimization substrate than current DARWIN search surfaces expose, but it is not yet the shared Stage-3 search object model.

### Decision

Promote the existing optimization substrate into the supported Stage-3 search object model, centered on:

- `OptimizationTarget`
- `MutableLocus`
- `SupportEnvelope`
- `MutationBounds`
- `CandidateBundle`
- `MaterializedCandidate`
- `PromotionDecision`
- `PromotionRecord`

### Ownership

- home: shared core-adjacent substrate, not kernel truth
- DARWIN consumes it for search/control
- lane adapters remain thin wrappers over lane-specific execution details

### Non-goals

- not a second DARWIN-only mutation ontology
- not a license to make every config field mutable
- not a runtime truth replacement

### Blast radius

- `BR3` while introduced as shared substrate and compiler target
- `BR4` if directly consumed by runtime pathways

### Exit criteria

- the optimization substrate is the canonical Stage-3 mutable-object vocabulary
- it clearly replaces or wraps thinner current search objects
- proving-lane mutation spaces can be expressed with bounded mutable loci

---

## ADR-03 — `ExecutionPlanV1` selective consumption semantics

### Status

Proposed.

### Problem

The current emitted `execution_plan` is useful for explanation but too shallow to support serious Stage-3 search over topology and execution structure.

### Decision

Advance `ExecutionPlan` from descriptive artifact to selectively consumable compiled execution closure.

### Truth boundary

- authored config remains human input
- `EffectiveConfig` remains compiled resolution
- `ExecutionPlan` becomes the compiled execution closure for selected runtime-adjacent consumers
- runtime event log remains singular runtime truth

### Consumption scope

Allowed early consumption:

- topology family selection
- workspace/tool binding inspection
- policy reference binding
- budget/environment binding lookup

Not allowed early consumption:

- replacing runtime truth
- arbitrary graph authoring
- introducing a second run-state model

### Blast radius

- `BR2` emitted only
- `BR4` once selected runtime paths consume it

### Exit criteria

- limited runtime consumption is proven without replay/parity regressions
- plan semantics are materially clearer than current baggy config behavior

---

## ADR-04 — `EvaluatorPackV1` plus `BudgetEnvelope`

### Status

Proposed.

### Problem

Stage-3 real inference will be expensive and noisy if evaluator semantics, control expectations, and budget comparability remain partly lane-local and partly caveated.

### Decision

Require a stronger `EvaluatorPackV1` with an explicit `BudgetEnvelope` for Stage-3 claim-bearing runs.

### `EvaluatorPackV1` should contain

- evaluator identity
- metric family and score semantics
- controls / perturbation requirements
- invalid-comparison rules
- claim-eligibility rules
- comparability notes

### `BudgetEnvelope` should contain

- budget class
- token / cost / wall-clock telemetry envelope
- allowed comparison class
- replication reserve policy

### Ownership

- DARWIN extension semantic truth
- BreadBoard may provide telemetry hooks

### Non-goals

- not a benchmark bureaucracy layer
- not a public evaluation package
- not a generic workflow engine

### Exit criteria

- Stage-3 claim-bearing runs cannot proceed without evaluator-pack closure
- cost and usage telemetry are strong enough to compare campaign arms

---

## ADR-05 — `DecisionLedgerV1` canonical scope

### Status

Proposed.

### Problem

Stage 3 cannot become component-centric if promotions, rollbacks, transfers, and deprecations remain spread across thin artifacts and derived views.

### Decision

Narrow `DecisionLedgerV1` into the canonical DARWIN decision truth for:

- component refs
- promotion decisions
- rollback decisions
- transfer cases and proofs
- deprecations

### Derived-only surfaces

The following remain derived-only:

- archive views
- comparative dossiers
- reviewer summaries
- replay dashboards
- external-safe packets

### Non-goals

- not a graph database project
- not archive-as-truth
- not duplication of runtime event truth

### Exit criteria

- Stage-3 promotions and transfers point to one canonical decision surface
- derived views remain derived

---

## ADR-06 — Model routing and spend classes

### Status

Proposed.

### Problem

Stage 3 needs real inference, but without a clear routing and spend policy the program will drift into uncontrolled cost or overuse of strong models.

### Decision

Adopt the following Stage-3 routing:

- `gpt-5.4-mini` = default search worker
- `gpt-5.4-nano` = bulk triage, filtering, and low-risk repeated worker
- stronger tier = tranche planning, hard arbitration, exceptional ambiguity only

### Spend doctrine

Use an initial campaign budget split of:

- `60%` main search arm
- `20%` replication reserve
- `10%` negative-control / perturbation reserve
- `10%` failure-analysis reserve

### Non-goals

- not a repo-wide provider-default rewrite
- not a strong-model-first search program
- not broad expensive campaigns before substrate uptake lands

### Exit criteria

- Stage-3 real-inference entry conditions are explicit
- model routing can be evaluated economically and operationally

---

## ADR-07 — Blast-radius and verification discipline

### Status

Proposed.

### Problem

Stage 3 is the first time DARWIN is likely to touch more runtime-adjacent behavior and real inference simultaneously. That raises the risk of silent scope creep and ambiguous regressions.

### Decision

Adopt the Stage-3 blast-radius classes:

- `BR0` docs/views only
- `BR1` additive emitters / validators
- `BR2` compiled outputs not consumed by runtime
- `BR3` evaluation or decision semantics
- `BR4` runtime-adjacent behavior change
- `BR5` parity-kernel change

### Required checks

- `BR2`: shadow proof required
- `BR3`: paired reruns + controls required
- `BR4`: replay/parity signoff + claim freeze
- `BR5`: explicit tranche approval only

### Verification doctrine

Every Stage-3 tranche should require:

1. compiler snapshot tests
2. replay/parity checks for `BR4+`
3. evaluator-pack fixtures
4. invalid-comparison fixtures
5. cost-telemetry checks
6. operator expected-value reporting
7. decision-ledger reconstruction checks
8. replicated run slices on primary lanes

### Exit criteria

- blast-radius rules are referenced by the execution plan
- Stage-3 changes can be classified and gated consistently
