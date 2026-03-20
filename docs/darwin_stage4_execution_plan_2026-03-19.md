# DARWIN Stage-4 Execution Plan

Date: 2026-03-19
Status: proposed Stage-4 execution entrypoint
Scope: Stage-4 doctrine freeze, live-economics hardening, runtime closure, deep bounded live search, and multi-family compounding
References:
- `docs/darwin_stage4_doctrine_2026-03-19.md`
- `docs/darwin_stage4_adr_set_2026-03-19.md`
- `docs/darwin_stage4_tranche1_slice_2026-03-19.md`
- `docs/darwin_stage4_tranche1_canary_status_2026-03-19.md`
- `docs/darwin_stage4_live_economics_slice_2026-03-20.md`
- `docs/darwin_stage4_comparison_envelope_slice_2026-03-20.md`
- `docs/darwin_stage4_repo_swe_ev_refinement_slice_2026-03-20.md`
- `docs/darwin_stage4_systems_live_slice_2026-03-20.md`
- `docs/darwin_stage4_deep_live_search_execution_plan_2026-03-20.md`
- `docs/darwin_stage4_deep_live_search_status_2026-03-20.md`
- `docs/darwin_stage4_route_mix_note_2026-03-20.md`
- `docs/darwin_stage4_strongest_families_note_2026-03-20.md`
- `docs/darwin_stage4_replay_note_2026-03-20.md`
- `docs/darwin_stage4_deep_live_search_review_2026-03-20.md`
- `docs/darwin_stage4_promotion_entry_gate_2026-03-20.md`
- `docs/darwin_stage4_multi_family_execution_plan_2026-03-20.md`
- `docs/darwin_stage4_multi_family_status_2026-03-20.md`
- `docs/darwin_stage4_second_family_decision_2026-03-20.md`
- `docs/darwin_stage4_family_watchdog_note_2026-03-20.md`
- `docs/darwin_stage4_atp_family_audit_note_2026-03-20.md`
- `docs/darwin_stage4_multi_family_review_2026-03-20.md`
- `docs/darwin_stage4_late_comparative_gate_2026-03-20.md`
- `docs/darwin_stage4_late_comparative_execution_plan_2026-03-20.md`
- `docs/darwin_stage4_canonical_artifacts_2026-03-20.md`
- `docs/darwin_stage4_claim_boundary_2026-03-20.md`
- `docs/darwin_stage4_registry_integrity_note_2026-03-20.md`
- `docs/darwin_stage4_transfer_eligibility_note_2026-03-20.md`
- `docs/darwin_stage4_replay_sufficiency_note_2026-03-20.md`
- `docs/darwin_stage4_transfer_comparability_note_2026-03-20.md`
- `docs/darwin_stage4_route_economics_note_2026-03-20.md`
- `docs/darwin_stage4_strongest_withheld_note_2026-03-20.md`
- `docs/darwin_stage4_late_comparative_review_2026-03-20.md`
- `docs/darwin_stage4_precloseout_gate_2026-03-20.md`
- `docs/darwin_stage4_remaining_to_close_2026-03-20.md`
- `docs/darwin_stage4_closeout_execution_plan_2026-03-20.md`
- `docs/darwin_stage4_signoff_2026-03-20.md`
- `docs/darwin_stage4_completion_gate_2026-03-20.md`
- `docs/darwin_stage4_future_roadmap_handoff_2026-03-20.md`
- `docs/darwin_stage4_main_verification_2026-03-20.md`
- `docs/darwin_stage4_systems_live_readiness_2026-03-20.md`
- `docs/darwin_stage4_cross_primary_lane_review_2026-03-20.md`
- `docs/darwin_stage4_deep_live_search_gate_2026-03-20.md`
- `docs/darwin_stage4_live_economics_status_2026-03-20.md`
- `docs/darwin_stage4_live_readiness_2026-03-20.md`
- `docs/darwin_stage4_search_policy_review_2026-03-20.md`
- `docs/darwin_stage4_search_policy_gate_2026-03-20.md`
- `docs/darwin_stage3_signoff_2026-03-19.md`
- `docs/darwin_stage3_completion_gate_2026-03-19.md`
- `docs_tmp/DARWIN/BB_DARWIN_PHASE_4_PLANNER_RESPONSE.md`

## 1. Purpose

This document converts the Stage-4 planner response into the first repo-local execution entrypoint.

The immediate goal is not to start broad live-provider campaigns tomorrow. The immediate goal is to define the smallest Stage-4 implementation program that can safely lead there.

## 2. Stage-4 mission

Stage 4 exists to turn the Stage-3 first-family proof into a live-provider, multi-family compounding program that discovers reusable improvements under bounded runtime, evaluator, and replay discipline.

## 3. Stage-4 boundary

### Must do

- freeze Stage-4 doctrine as distinct from Stage-3 closeout
- define the Stage-4 primitive program
- define live vs scaffold claim eligibility
- define campaign classes and spend discipline
- define primary proving lanes and audit lanes
- define hard gates for runtime-adjacent and live-economics work
- define the first tranche sequence for multi-family compounding

### Must not do

- no reopening of Stage-3 closeout artifacts
- no new runtime truth
- no async/distributed substrate as a leading strategy
- no broad lane expansion in early Stage 4
- no public or superiority package work
- no ATP-centered roadmap
- no topology-DSL project

## 4. Stage-4 primitive program

Stage 4 should begin from exactly three top-level primitive families:

1. `ExecutionEnvelopeV2`
2. `SearchPolicyV1`
3. `ComponentFamilyV1`

Supporting upgrades are authorized only as narrow extensions of current truth surfaces:

- provider-backed `BudgetEnvelope` and `EvaluatorPack` hardening
- `DecisionLedgerV1.1`
- small BreadBoard telemetry and plan-consumption hooks

These are the only first-class primitive families this entrypoint authorizes.

## 5. Program structure

### Tranche 0 — Reset and live-claim doctrine

Objective:
- start Stage 4 as a clean new program

Includes:
- doctrine freeze
- live vs scaffold claim boundary
- campaign classes and routing doctrine
- lane matrix freeze
- ADR set
- blast-radius and claim-escalation rules

Hard gate:
- no Stage-3 artifact is reopened
- live/scaffold distinction is explicit in doctrine
- no new runtime truth is introduced
- no lane is added

### Tranche 1 — Live economics and runtime closure

Objective:
- turn Stage-3 substrate coherence into Stage-4 live-operational credibility

Includes:
- `ExecutionEnvelopeV2` first slice
- provider-backed `BudgetEnvelope` upgrade
- support-envelope and mutation-bound enforcement
- evaluator-pack version hardening
- deeper plan-consumption hooks on authorized paths
- live/scaffold separation in reporting and gates

Hard gate:
- claim-bearing live arms have provider-backed telemetry
- matched-budget comparison contract is materially tighter
- support-envelope widening is blocked or surfaced
- no BR4 runtime or replay regressions
- runtime/execution-plan mismatch rate is materially reduced

### Tranche 2 — Deep bounded live search

Objective:
- run the first serious live-provider search program

Includes:
- `SearchPolicyV1` operational pilot on `lane.repo_swe` and `lane.systems`
- repeated matched-budget live campaigns
- operator and topology EV from live arms, not only canaries
- second-family and third-family hunt
- family priors begin influencing candidate generation

Hard gate:
- at least one operator family and one topology family show positive live EV
- second family reaches at least provisional status, ideally promoted
- mini/nano routing economics are interpretable
- systems lane remains interpretable under bounded spend

### Tranche 3 — Multi-family promotion and bounded transfer matrix

Objective:
- prove that Stage 4 is about compounding, not just search depth

Includes:
- promote second and possibly third family
- run a bounded transfer matrix across the primary and confirmation lanes
- operationalize `ComponentFamilyV1`
- record retained and failed transfers in one canonical family program
- authorize family composition canary only if at least two families are real

Hard gate:
- at least two promoted families total
- at least two retained bounded transfers or equivalent reuse signals
- family registry/state is operationally consumed by search or transfer logic
- failed-transfer taxonomy remains informative

### Tranche 4 — Comparative proof and late-stage options

Objective:
- package what Stage 4 actually earned and decide whether Stage 5 should take on async/distributed or broader lane work

Includes:
- strong internal comparative dossier / review layer
- no superiority theater
- optional async/distributed design spike only if throughput bottleneck is proven
- optional fresher Repo_SWE slice only if economics and evaluator discipline are strong

Hard gate:
- multi-family compounding is real
- live economics are real
- component reuse visibly accelerates later search
- no core contamination

## 6. Proving-lane matrix

### Live-economics and runtime tranche

- primary: `lane.repo_swe`
- secondary: `lane.harness`
- systems canary: `lane.systems`
- audit: `lane.atp`

### Deep-search tranche

- primary: `lane.repo_swe`, `lane.systems`
- watchdog/control: `lane.harness`
- confirmation: `lane.scheduling`
- audit: `lane.atp`

### Deferred centrality

- `lane.research`

## 7. First execution epics

### Epic 1 — Live economics hardening

- provider-backed telemetry
- execution-mode separation
- budget-envelope upgrade
- cost-source semantics

### Epic 2 — `ExecutionEnvelopeV2`

- deeper plan consumption
- support-envelope enforcement
- mutation-bound enforcement
- topology parameter closure

### Epic 3 — `SearchPolicyV1` pilot

- operator and topology family priors
- campaign-class chooser
- arm allocation
- abort logic

### Epic 4 — Component family program

- family lifecycle state
- family promotion gate
- family transfer gate
- family deprecation logic

### Epic 5 — Deep live campaigns

- Repo_SWE primary
- Systems primary
- Harness control
- Scheduling confirmation

## 8. First work packages

### Canary packages

- provider-backed telemetry on Repo_SWE live arms
- support-envelope enforcement on Harness and Repo_SWE
- one operator-family pilot on Repo_SWE
- one Systems live-budget pilot
- evaluator-pack version lock enforcement

### Major bets

- `SearchPolicyV1` operational on both primary lanes
- second-family promotion
- third-family hunt
- bounded transfer matrix
- family composition canary if authorized

## 9. Verification doctrine

Every Stage-4 tranche should require the minimum set that supports real decisions:

1. live/scaffold separation checks
2. provider-backed telemetry validation on claim-bearing arms
3. replay/parity checks for BR4+ changes
4. evaluator-pack version and invalid-comparison fixtures
5. support-envelope and mutation-bound enforcement checks
6. operator/topology EV reporting
7. decision-ledger and family-lifecycle reconstruction checks
8. replicated run slices on primary lanes

## 10. Stage-4 success criteria

Stage 4 should be judged by these end-state outcomes:

- at least 2–3 promoted component families, not one
- at least two retained bounded transfers or equivalent reuse signals
- live-provider mini/nano economics are real and interpretable
- Repo_SWE and Systems produce repeated gains under matched-budget comparison
- a rising share of new wins reuse already-promoted families
- BreadBoard core remains singular in runtime truth

## 11. Immediate next step

The next move is:

1. freeze Stage-4 doctrine
2. freeze the ADR set
3. define the first implementation slice for Tranche 1, starting with live-economics hardening and `ExecutionEnvelopeV2` boundary work
