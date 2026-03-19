# DARWIN Stage-3 Execution Plan

Date: 2026-03-19
Status: tranche-1 hard gate passed
Scope: Stage-3 doctrine freeze, tranche-1 substrate uptake, and bounded real-inference entry conditions
References:
- `docs/darwin_stage3_doctrine_2026-03-19.md`
- `docs/darwin_stage3_adr_set_2026-03-19.md`
- `docs/darwin_stage3_tranche1_slice_2026-03-19.md`
- `docs/darwin_stage3_optimize_object_mapping_2026-03-19.md`
- `docs/darwin_stage3_tranche1_uptake_review_2026-03-19.md`
- `docs/darwin_stage3_tranche1_uptake_gate_2026-03-19.md`
- `docs_tmp/DARWIN/BB_DARWIN_PHASE_3_PLANNER_RESPONSE.md`

## 1. Purpose

This document converts the Stage-3 planner response into the first repo-local execution entrypoint.

The immediate goal is not to start broad real-inference search tomorrow. The immediate goal is to define the smallest Stage-3 implementation program that can safely lead there.

## 2. Stage-3 mission

Stage 3 exists to turn BreadBoard + DARWIN from a bounded, replay-native experimentation layer into an inference-native optimization substrate for reusable components.

## 3. Stage-3 boundary

### Must do

- freeze Stage-3 doctrine as distinct from current-program closeout
- define the Stage-3 primitive program
- define tranche-0 and tranche-1 execution scope
- define lane taxonomy and proving roles
- define blast-radius and verification rules
- define initial real-inference entry conditions

### Must not do

- no reopening of current-program closure artifacts
- no new runtime truth
- no async runtime expansion
- no lane expansion in early Stage 3
- no public or superiority package work
- no ATP-centered roadmap

## 4. Stage-3 primitive program

Stage 3 should begin from exactly four primitive families:

1. `OptimizationSubstrateV1`
2. `ExecutionPlanV1`
3. `EvaluatorPackV1` plus `BudgetEnvelope`
4. `DecisionLedgerV1`

These are the only first-class primitive families this entrypoint authorizes.

## 5. Program structure

### Tranche 0 — Reset and doctrine freeze

Objective:
- start Stage 3 as a clean new program

Includes:
- doctrine freeze
- lane taxonomy freeze
- model-routing and spend doctrine
- ADR set
- blast-radius rules

Hard gate:
- no current-program artifact is reopened
- no new runtime truth is introduced
- no new lane is launched

### Tranche 1 — Substrate uptake

Objective:
- turn the right Phase-2 read-model work into live Stage-3 substrate leverage

Includes:
- promote the optimization substrate into the shared Stage-3 search object model
- make `ExecutionPlan` selectively consumable on limited paths
- require stronger `EvaluatorPack` semantics on Stage-3 claim-bearing runs
- add cost and usage telemetry hooks
- narrow `DecisionLedger` to canonical Stage-3 decision truth

Hard gate:
- limited `ExecutionPlan` consumption works without runtime regressions
- mutable loci exist for at least `lane.repo_swe` and `lane.systems`
- evaluator packs are required on Stage-3 claim-bearing runs
- cost telemetry is good enough to compare campaign arms
- no replay/parity regressions appear

### Tranche 2 — Bounded real-inference search

Objective:
- start real model search under tight economics and evaluator discipline

Includes:
- `gpt-5.4-mini` default search campaigns
- `gpt-5.4-nano` bulk triage/filter worker use
- operator expected-value measurement
- topology-parameter expected-value measurement
- bounded campaigns on the primary lanes

Hard gate:
- at least one operator or topology family shows positive expected value across repeated campaigns
- at least one lane shows real gain under matched budgets
- mini/nano routing economics are interpretable
- no evaluator-control collapse occurs

### Tranche 3 — Component promotion and bounded transfer

Objective:
- convert search wins into reusable substrate learning

Includes:
- first promoted Stage-3 component families
- bounded transfer program across the primary/secondary lanes
- failed-transfer taxonomy
- `DecisionLedger` as canonical decision truth

Hard gate:
- at least one component family retains value outside its source lane or source benchmark family
- at least one failed transfer is recorded usefully
- promotion stays replay-backed
- archive does not drift back toward canonical truth

## 6. Proving-lane matrix

### Foundation tranche

- primary: `lane.harness`, `lane.repo_swe`
- secondary: `lane.scheduling`
- audit: `lane.atp`

### Real-search tranche

- primary: `lane.repo_swe`, `lane.systems`
- secondary: `lane.scheduling`
- watchdog/control: `lane.harness`
- audit: `lane.atp`

### Deferred centrality

- `lane.research`

## 7. Initial work packages

### WP-01 — Freeze Stage-3 doctrine

Deliverables:
- doctrine doc
- tranche structure
- lane taxonomy
- build-now / later / reject table

### WP-02 — Freeze Stage-3 ADR set

Deliverables:
- ADRs for the four primitive families
- ADR for routing/spend classes
- ADR for blast-radius and verification rules

### WP-03 — Define substrate-uptake implementation slice

Deliverables:
- precise ownership and boundary for `OptimizationSubstrateV1`
- selective `ExecutionPlan` consumption scope
- `EvaluatorPack` + `BudgetEnvelope` scope
- `DecisionLedger` canonical subset scope

### WP-04 — Define bounded real-inference entry conditions

Deliverables:
- primary lanes
- initial search spaces
- model routing classes
- spend bucket rules
- tranche-2 go/no-go conditions

## 8. Verification doctrine

Every Stage-3 tranche should require:

1. compiler snapshot tests
2. replay/parity checks for BR4+
3. evaluator-pack fixtures
4. invalid-comparison fixtures
5. cost-telemetry checks
6. operator expected-value reporting
7. decision-ledger reconstruction checks
8. replicated run slices on primary lanes

## 9. Next step

The next correct move after tranche-1 hard-gate passage is:

- start the bounded real-inference entry tranche

That tranche should focus on:

- `lane.repo_swe` and `lane.systems` as primary lanes
- `gpt-5.4-mini` as the default worker model
- `gpt-5.4-nano` for bulk/filter/repetition roles
- fixed replication and control reserve behavior
- operator expected-value reporting under matched bounded budgets

Current tranche-1 slice reference:

- `docs/darwin_stage3_tranche1_slice_2026-03-19.md`
- `docs/darwin_stage3_tranche1_canary_status_2026-03-19.md`
- `docs/darwin_stage3_optimize_object_mapping_2026-03-19.md`
- `docs/darwin_stage3_tranche1_uptake_review_2026-03-19.md`
- `docs/darwin_stage3_tranche1_uptake_gate_2026-03-19.md`
- `docs/darwin_stage3_tranche1_budget_ledger_status_2026-03-19.md`
- `docs/darwin_stage3_tranche1_mutation_canary_status_2026-03-19.md`
- `docs/darwin_stage3_tranche1_boundary_2026-03-19.md`
- `docs/darwin_stage3_mutation_canary_review_2026-03-19.md`
- `docs/darwin_stage3_mutation_canary_gate_2026-03-19.md`
- `docs/darwin_stage3_substrate_ownership_note_2026-03-19.md`
- `docs/darwin_stage3_systems_mutation_canary_status_2026-03-19.md`
- `docs/darwin_stage3_cost_routing_readiness_2026-03-19.md`
- `docs/darwin_stage3_secondary_lane_confirmation_2026-03-19.md`
- `docs/darwin_stage3_atp_audit_note_2026-03-19.md`
- `docs/darwin_stage3_tranche1_verification_bundle_2026-03-19.md`
- `docs/darwin_stage3_tranche1_hard_gate_2026-03-19.md`
- `docs/darwin_stage3_real_inference_entry_conditions_2026-03-19.md`
