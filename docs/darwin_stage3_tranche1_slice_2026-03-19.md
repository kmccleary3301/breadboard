# DARWIN Stage-3 Tranche-1 Implementation Slice

Date: 2026-03-19
Status: implementation slice defined
Scope: smallest implementation-ready Stage-3 tranche-1 slice
References:
- `docs/darwin_stage3_doctrine_2026-03-19.md`
- `docs/darwin_stage3_execution_plan_2026-03-19.md`
- `docs/darwin_stage3_adr_set_2026-03-19.md`

## 1. Purpose

This document defines the **smallest buildable Stage-3 tranche-1 slice**.

It is intentionally narrower than the full Stage-3 tranche-1 description in the execution plan. The goal is to identify the first real implementation boundary that increases substrate quality without starting broad real-inference work too early.

## 2. Slice mission

The tranche-1 slice mission is:

> promote the existing optimization substrate into the Stage-3 search vocabulary, add limited `ExecutionPlan` consumption on narrow paths, tighten evaluator and budget closure for claim-bearing Stage-3 runs, and narrow decision truth to one canonical DARWIN surface.

## 3. What this slice includes

### Included

1. `OptimizationSubstrateV1` ownership and uptake
2. limited `ExecutionPlanV1` consumption
3. `EvaluatorPackV1` plus `BudgetEnvelope` uptake
4. `DecisionLedgerV1` narrowing
5. proving-lane and verification definition for the first code tranche

### Excluded

- no broad real-inference campaigns
- no async substrate
- no new lane launch
- no public or superiority packaging
- no archive redesign
- no research-lane centrality
- no ATP-led primitive design

## 4. Ownership and code-touch targets

The point of this slice is to map Stage-3 primitive work onto the current codebase precisely.

### 4.1 `OptimizationSubstrateV1`

**Primary ownership**

- `agentic_coder_prototype/optimize/substrate.py`
- `agentic_coder_prototype/optimize/backend.py`
- `agentic_coder_prototype/optimize/promotion.py`
- `agentic_coder_prototype/optimize/__init__.py`

**Stage-3 role**

- become the canonical mutable-object vocabulary for Stage-3 search
- provide the shared substrate DARWIN consumes instead of inventing a parallel ontology

**Allowed tranche-1 work**

- formalize which of the existing substrate objects become Stage-3 canonical
- define repo_swe and systems-facing mutable-locus templates
- define support-envelope usage for transfer and promotion
- define mutation-bounds requirements for Stage-3 search

**Explicitly not in this slice**

- broad mutation-operator expansion
- runtime rewriting around the substrate
- general mutation of arbitrary config fields

### 4.2 `ExecutionPlanV1`

**Primary ownership**

- `breadboard_ext/darwin/phase2.py`
- `scripts/run_darwin_t1_live_baselines_v1.py`
- `agentic_coder_prototype/execution/sequential_executor.py`
- `agentic_coder_prototype/compilation/*` if touched later by follow-up slices

**Stage-3 role**

- move `execution_plan` from emitted explanation toward selective runtime-adjacent consumption

**Allowed tranche-1 work**

- consume compiled topology-family selection
- consume workspace/tool binding inspection
- consume policy/budget/environment binding lookups
- keep runtime event log as the only runtime truth

**Explicitly not in this slice**

- any second run-state truth
- arbitrary graph authoring
- generalized topology DSL work

### 4.3 `EvaluatorPackV1` plus `BudgetEnvelope`

**Primary ownership**

- `breadboard_ext/darwin/phase2.py`
- `breadboard_ext/darwin/contracts.py`
- `scripts/run_darwin_t1_live_baselines_v1.py`
- `scripts/run_darwin_t1_baseline_scorecard_v1.py`
- any Stage-3-specific cost-usage adapter that grows from current formal-pack usage surfaces

**Likely telemetry input surfaces**

- `scripts/run_bb_formal_pack_v1.py`
- current usage-ledger and provider-metrics outputs

**Stage-3 role**

- make claim-bearing Stage-3 runs evaluator-closed and budget-comparable before real search spend ramps up

**Allowed tranche-1 work**

- require evaluator-pack presence on Stage-3 claim-bearing runs
- add budget-envelope structure
- connect token/cost/wall-clock telemetry into one comparison-ready envelope
- define replication reserve and control expectations

**Explicitly not in this slice**

- public benchmark packaging
- broad lane-wide evaluator redesign
- economics claims beyond bounded operational comparison

### 4.4 `DecisionLedgerV1`

**Primary ownership**

- `breadboard_ext/darwin/ledger.py`
- `scripts/build_darwin_evolution_ledger_v0.py`
- `scripts/build_darwin_transfer_family_view_v0.py`
- `scripts/build_darwin_lineage_review_v0.py`

**Stage-3 role**

- become the canonical decision truth for promotions, rollbacks, transfers, and deprecations

**Allowed tranche-1 work**

- narrow ledger scope to canonical decision truth
- explicitly demote archive views to derived-only status
- define component refs and decision kinds required by Stage-3 substrate work

**Explicitly not in this slice**

- graph-database expansion
- archive-as-truth
- duplicating runtime event truth

## 5. Proving lanes for this slice

### Primary

- `lane.harness`
- `lane.repo_swe`

### Secondary

- `lane.scheduling`

### Audit-only

- `lane.atp`

### Deferred from this slice

- `lane.systems` for runtime-heavy proving
- `lane.research`

Rationale:

- `lane.harness` is the lowest-risk control lane for limited `ExecutionPlan` consumption and evaluator closure
- `lane.repo_swe` is the best lane for substrate pressure and later real-search centrality
- `lane.scheduling` is the cleanest secondary confirmation lane
- `lane.atp` should only pressure semantic discipline here

## 6. Exact tranche-1 slice work packages

### WP-01 — Formalize `OptimizationSubstrateV1`

Deliverables:

- one ownership/boundary note turning the existing optimize objects into Stage-3 canonical search vocabulary
- one mapping from current DARWIN search surfaces to substrate objects
- one proving-lane mutable-locus template for `lane.repo_swe`
- one proving-lane mutable-locus template for future `lane.systems`

Success condition:

- Stage-3 mutation spaces can be described in substrate terms instead of ad hoc DARWIN wrapper terms

Blast radius:

- `BR3`

### WP-02 — Limited `ExecutionPlan` consumption

Deliverables:

- one narrow runtime-adjacent consumption path for compiled topology/tool/workspace/policy bindings
- explicit non-consumed fields list
- shadow-vs-consumed parity checks on the proving lanes

Success condition:

- compiled plan improves execution clarity without becoming a second runtime truth

Blast radius:

- `BR4`

### WP-03 — `EvaluatorPackV1` plus `BudgetEnvelope`

Deliverables:

- required evaluator-pack presence on Stage-3 claim-bearing runs
- budget-envelope structure with token, cost, wall-clock fields
- replication and negative-control reserve policy fields
- lane-specific proving coverage for `lane.harness` and `lane.repo_swe`

Success condition:

- Stage-3 claim-bearing comparisons cannot proceed on the proving lanes without evaluator-pack and budget-envelope closure

Blast radius:

- `BR3`

### WP-04 — Narrow `DecisionLedgerV1`

Deliverables:

- decision-ledger scope note
- required decision kinds for Stage-3 tranche-1
- derived-only archive rule restated and enforced in docs/tests where needed

Success condition:

- promotions, rollbacks, and transfers point to one canonical decision surface for Stage-3 tranche-1 work

Blast radius:

- `BR3`

### WP-05 — Verification and tranche gate

Deliverables:

- compiler snapshot expectations
- replay/parity checks for the limited `ExecutionPlan` consumption path
- evaluator-pack fixtures
- cost-telemetry checks
- decision-ledger reconstruction checks
- tranche-1 hard gate note

Success condition:

- the slice proves that Stage 3 can move one layer inward without destabilizing BreadBoard runtime truth

Blast radius:

- mixed `BR2` / `BR3` / `BR4`

## 7. Hard gate for this slice

This slice is complete only when all of the following are true:

- `OptimizationSubstrateV1` is the stated Stage-3 mutable-object vocabulary
- limited `ExecutionPlan` consumption is defined narrowly enough to test safely
- evaluator-pack plus budget-envelope closure is mandatory for Stage-3 claim-bearing proving runs
- the decision ledger remains the only canonical DARWIN decision truth
- archive remains derived-only
- proving lanes and audit lane are explicit
- verification expectations are explicit before code starts

## 8. What this slice still does not authorize

Even when complete, this slice does **not** authorize:

- broad real-inference campaigns
- async search
- stage-wide lane expansion
- public or superiority evidence work
- systems-heavy proving before the substrate-uptake gate is satisfied

## 9. Immediate next move after this slice

After this slice is accepted, the next implementation step should be:

- turn `WP-01` and `WP-02` into the first code tranche

That code tranche should stay narrowly focused on:

- substrate uptake
- limited `ExecutionPlan` consumption
- evaluator/budget closure on the proving lanes

