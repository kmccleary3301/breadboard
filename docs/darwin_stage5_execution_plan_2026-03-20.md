# DARWIN Stage-5 Execution Plan

Date: 2026-03-20
Status: repeated multi-lane gate passed
Scope: Stage-5 doctrine freeze, economics truth hardening, compounding protocol, family-aware search, and scalable compounding proof
References:
- `docs/darwin_stage5_doctrine_2026-03-20.md`
- `docs/darwin_stage5_adr_set_2026-03-20.md`
- `docs/darwin_stage5_tranche1_slice_2026-03-20.md`
- `docs/darwin_stage5_tranche1_canary_status_2026-03-20.md`
- `docs/darwin_stage5_tranche1_status_2026-03-21.md`
- `docs/darwin_stage5_tranche1_review_2026-03-21.md`
- `docs/darwin_stage5_tranche1_hard_gate_2026-03-21.md`
- `docs/darwin_stage5_secondary_lane_slice_2026-03-21.md`
- `docs/darwin_stage5_secondary_lane_status_2026-03-21.md`
- `docs/darwin_stage5_multilane_slice_2026-03-21.md`
- `docs/darwin_stage5_multilane_status_2026-03-21.md`
- `docs/darwin_stage5_multilane_review_2026-03-21.md`
- `docs/darwin_stage5_multilane_gate_2026-03-21.md`
- `docs/darwin_stage4_signoff_2026-03-20.md`
- `docs/darwin_stage4_completion_gate_2026-03-20.md`
- `docs/darwin_stage4_future_roadmap_handoff_2026-03-20.md`
- `docs_tmp/DARWIN/phase_5/BB_DARWIN_PHASE_5_PLANNER_RESPONSE.md`

## 1. Purpose

This document converts the Stage-5 planner response into the first repo-local execution entrypoint.

The immediate goal is not broad scale. The immediate goal is to define the smallest Stage-5 implementation program that can prove whether compounding is becoming real and measurable.

## 2. Stage-5 mission

Stage 5 exists to prove that promoted families measurably improve later search outcomes under live-provider economics.

## 3. Stage-5 boundary

### Must do

- freeze Stage-5 doctrine as distinct from Stage-4 closeout
- define the Stage-5 primitive program
- define the compounding protocol and comparison modes
- define provider-backed economics truth and route segmentation rules
- define the primary lane classes and proving order
- define the tranche sequence for scalable compounding

### Must not do

- no reopening of Stage-4 closeout artifacts
- no new runtime truth
- no async/distributed substrate as a leading strategy
- no broad lane expansion early in Stage 5
- no public or superiority package work
- no ATP-centered roadmap
- no giant topology language

## 4. Stage-5 primitive program

Stage 5 should begin from exactly four typed primitives:

1. `ExecutionEnvelopeV3`
2. `SearchPolicyV2`
3. `ComponentFamilyV2`
4. `CompoundingCaseV1`

Supporting upgrades are authorized only as narrow extensions of current truth surfaces:

- provider-backed cost and route truth hardening
- matched-budget and comparison-truth tightening
- shared optimize-substrate uptake
- narrow `DecisionLedger` compounding extensions

These are the only first-class primitive families this entrypoint authorizes.

## 5. Program structure

### Tranche 0 — Stage-5 reset and compounding doctrine

Objective:
- start Stage 5 as a clean new program

Includes:
- doctrine freeze
- compounding thesis and success metrics
- live/scaffold doctrine carryover
- lane class freeze
- OpenRouter economics stance
- ADR set

Hard gate:
- no Stage-4 artifact is reopened
- no new runtime truth is introduced
- no new domain lane is added
- `CompoundingCaseV1` protocol is frozen

### Tranche 1 — Economics truth and compounding protocol

Objective:
- make provider economics and comparison truth strong enough for Stage-5 claims

Includes:
- provider-backed cost surface hardening
- route/provider fallback reason canonicalization
- cache-field truth and cost-source classification
- matched-budget/comparison truth tightening
- `CompoundingCaseV1`
- `SearchPolicyV2` skeleton
- `ComponentFamilyV2` lifecycle draft

Hard gate:
- claim-bearing live rows have provider-backed or explicitly classified cost truth
- fallback reason codes and provider origin are canonical
- at least one warm-start vs lockout protocol run can be built cleanly
- no BR4 replay/parity regressions
- comparison invalidity is machine-blocked where possible

### Tranche 2 — Family-aware search becomes operational

Objective:
- make `SearchPolicyV2` actually use families and prove first compounding lift

Includes:
- `SearchPolicyV2` operational on Repo_SWE and Systems
- `cold_start`, `warm_start`, and `family_lockout` modes
- family priors influence allocation
- family-aware operator/topology selection
- repeated live comparisons dense enough for attribution
- target either a second retained transfer or a third promoted family

Hard gate:
- warm-start beats matched lockout on at least one primary lane and does not regress badly on the other
- at least one additional family reaches promoted or retained status
- provider economics are interpretable enough to compare modes
- Systems lane remains interpretable

### Tranche 3 — Scaled compounding and bounded transfer matrix

Objective:
- show that compounding is not just a one-lane or one-family effect

Includes:
- add 1–2 more promoted families
- run a bounded transfer matrix across Repo_SWE, Systems, and Scheduling
- expand retained transfer cases
- evolve the failed-transfer taxonomy
- allow a narrow family-composition canary only if preconditions are met

Hard gate:
- total promoted families reach 4–6
- multiple retained transfers exist
- reuse share of successful/promotable candidates rises materially
- at least one compounding-rate metric improves over time
- family registry is operational, not ceremonial

### Tranche 4 — Late comparative and Stage-6 decision tranche

Objective:
- package what Stage 5 genuinely earned and decide whether Stage 6 should take on broader search scale, fresher slices, or async/distributed work

Includes:
- comparative dossier
- compounding-rate memo
- route economics review
- decision on clean OpenRouter-first or explicit dual-provider economics
- decision on Research consumer slice
- decision on async/distributed readiness

Hard gate:
- compounding rate is measurable and positive
- family-aware search clearly changes later search outcomes
- economics are not the main caveat anymore
- no core contamination

## 6. Proving-lane matrix

### Tranche 1

- primary: `lane.repo_swe`
- secondary: `lane.harness`
- systems canary: `lane.systems`
- audit: `lane.atp`

### Tranche 2

- primary: `lane.repo_swe`, `lane.systems`
- control: `lane.harness`
- confirmation: `lane.scheduling`
- audit: `lane.atp`

### Later consumer lane

- `lane.research`

## 7. First execution epics

### Epic 1 — Economics truth

- provider-backed cost surface hardening
- fallback-reason canonicalization
- cache-field truth
- route/provider segmentation rules

### Epic 2 — Compounding protocol

- `CompoundingCaseV1`
- comparison-mode protocol
- warm-start / lockout controls
- compounding metrics

### Epic 3 — `SearchPolicyV2` skeleton

- operator priors
- family priors
- route priors
- campaign-class chooser
- abort thresholds

### Epic 4 — `ComponentFamilyV2`

- family lifecycle draft
- family scope/support-envelope semantics
- transfer eligibility state
- retained/deprecated criteria

### Epic 5 — Family-aware search

- Repo_SWE primary proving
- Systems follow-on proving
- Harness controls
- Scheduling confirmation

## 8. First work packages

### Initial packages

- provider-backed cost truth on Repo_SWE live rows
- canonical warm-start / family-lockout protocol
- `CompoundingCaseV1` emission
- `SearchPolicyV2` skeleton consuming current family state
- `ComponentFamilyV2` lifecycle draft with no runtime-truth expansion

## 9. Immediate next step

The next implementation move is:

1. write the smallest Stage-5 tranche-1 slice
2. map it onto exact code-touch ownership
3. begin with economics truth + compounding protocol on Repo_SWE
