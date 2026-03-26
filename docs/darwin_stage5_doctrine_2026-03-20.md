# DARWIN Stage-5 Doctrine

Date: 2026-03-20
Status: proposed Stage-5 doctrine and execution entrypoint
References:
- `docs_tmp/DARWIN/phase_5/BB_DARWIN_PHASE_5_PLANNER_RESPONSE.md`
- `docs/darwin_stage4_signoff_2026-03-20.md`
- `docs/darwin_stage4_completion_gate_2026-03-20.md`
- `docs/darwin_stage4_future_roadmap_handoff_2026-03-20.md`

## 1. Purpose

This document starts Stage 5 as a **new BreadBoard + DARWIN program**.

It does not reopen Stage 4. It defines the doctrine that should govern Stage-5 planning and implementation.

## 2. Stage-5 mission

The Stage-5 mission is:

> turn BreadBoard + DARWIN from a bounded compounding proof into a live-economics, family-aware search system whose promoted families measurably improve later search outcomes.

## 3. What Stage 5 is trying to do

Stage 5 exists to prove **scalable compounding**, not merely more search activity.

That means:

- family-aware search must beat matched lockout or cold-start controls
- promoted families must improve later search efficiency, promotion velocity, or retained-transfer yield
- provider-backed economics must be strong enough to support bounded compounding claims
- family state must become operationally consumed by search allocation and transfer logic
- BreadBoard runtime truth must remain singular and uncontaminated

## 4. What Stage 5 must not become

Stage 5 must not become:

- a stealth continuation of Stage-4 closeout work
- a campaign-scaling program without measurable reuse lift
- a family-registry growth program without operational leverage
- a new artifact-packaging program
- an ATP-centered roadmap
- a broad lane-expansion program
- an async/distributed-first program
- a second runtime truth
- a topology-DSL project

## 5. Global Stage-5 rules

1. Stage-4 closure on `main` remains frozen and must not be reopened.
2. Runtime truth remains singular and BreadBoard-owned.
3. Only `execution_mode=live` campaigns count toward Stage-5 power claims.
4. Scaffold-mode runs remain useful for substrate testing and artifact generation, not compounding claims.
5. Stage 5 proves compounding only when warm-start family-aware campaigns beat matched family-lockout or cold-start controls.
6. Repo_SWE and Systems are the primary proving lanes.
7. Harness remains watchdog/control.
8. Scheduling remains a confirmation and transfer-clarification lane.
9. ATP remains audit-only.
10. Research remains deferred from centrality early in Stage 5.
11. Any BR4 runtime-adjacent regression freezes claim escalation until replay/parity clears.

## 6. Stage-5 lane classes

### Primary search lanes

- `lane.repo_swe`
- `lane.systems`

### Confirmation lanes

- `lane.scheduling`
- `lane.harness`

### Audit lane

- `lane.atp`

### Deferred consumer lane

- `lane.research`

## 7. Stage-5 primitive thesis

Stage 5 should stay focused on the smallest high-leverage primitive program:

1. `ExecutionEnvelopeV3`
2. `SearchPolicyV2`
3. `ComponentFamilyV2`

And exactly one small new typed record:

4. `CompoundingCaseV1`

Supporting upgrades are authorized only as narrow extensions of current truth surfaces:

- provider-backed cost and route truth hardening
- matched-budget and comparison-truth tightening
- shared optimize-substrate uptake
- narrow `DecisionLedger` extensions for compounding and family lifecycle

Everything else should stay either:

- existing runtime truth
- compiled view
- or derived artifact

## 8. Stage-5 model-routing doctrine

Stage 5 should assume:

- `gpt-5.4-mini` = default live search worker
- `gpt-5.4-nano` = scout, lockout, triage, and repeated low-risk worker
- stronger tier = exceptional ambiguity, hard systems mutation, or arbitration only

OpenRouter remains preferred when healthy and authorized.

If OpenRouter and direct OpenAI behavior diverge materially, Stage 5 should segment those economics explicitly rather than pretending one clean provider surface exists.

## 9. Compounding protocol doctrine

Every serious Stage-5 family-aware campaign class should support:

- `cold_start`
- `warm_start`
- `family_lockout`

Warm-start beating lockout on matched live arms is the canonical Stage-5 compounding test.

Campaign count, promotion count, registry growth, and transfer count are not by themselves evidence of scalable compounding.

## 10. Build-now / later / reject

### Build now

- economics truth hardening
- `CompoundingCaseV1`
- `SearchPolicyV2` skeleton
- `ComponentFamilyV2` lifecycle draft
- warm-start / lockout protocol on Repo_SWE first
- narrow shared optimize-substrate uptake plan

### Build later

- family-aware search on Systems after Repo_SWE tranche-1 closure
- broader bounded transfer matrix after compounding lift is real
- narrow family-composition canary only after multiple retained families exist
- narrow Research consumer slice after primary-lane compounding is stable

### Reject for early Stage 5

- new runtime truth
- archive as canonical truth
- giant topology DSL
- config mini-language
- ATP-first roadmap
- new domain lanes
- async/distributed leading strategy
- family inventory growth treated as compounding proof
- strong-tier default routing

## 11. Immediate next step

The next implementation move is not broad coding.

The next move is:

1. freeze Stage-5 doctrine
2. write the initial ADR set
3. define the Stage-5 execution plan and tranche structure against those ADRs
