# DARWIN Stage-3 Doctrine

Date: 2026-03-19
Status: proposed Stage-3 doctrine and execution entrypoint
References:
- `docs_tmp/DARWIN/BB_DARWIN_PHASE_3_PLANNER_RESPONSE.md`
- `docs/darwin_current_program_signoff_2026-03-19.md`
- `docs/darwin_current_program_completion_gate_2026-03-19.md`

## 1. Purpose

This document starts Stage 3 as a **new BreadBoard + DARWIN program**.

It does not reopen the closed current program. It defines the doctrine that should govern Stage-3 planning and implementation.

## 2. Stage-3 mission

The Stage-3 mission is:

> turn BreadBoard + DARWIN into an inference-native, component-compounding optimization substrate that discovers reusable improvements under bounded search, explicit evaluator packs, and replay-backed promotion.

## 3. What Stage 3 is trying to do

Stage 3 exists to increase **real capability density**, not merely packaging quality.

That means:

- stronger search object quality
- stronger runtime-adjacent execution semantics
- bounded real-inference search under cost discipline
- reusable component promotion rather than one-off candidate wins

## 4. What Stage 3 must not become

Stage 3 must not become:

- a stealth continuation of Phase-2 closeout work
- a new artifact-packaging program
- an ATP-centered roadmap
- a large async/distributed campaign before substrate uptake is proven
- a config-language project
- an archive-as-truth redesign

## 5. Global Stage-3 rules

1. Current-program closure remains frozen and must not be reopened.
2. Runtime truth remains singular and BreadBoard-owned.
3. DARWIN remains the control/evaluation/decision layer, not a replacement runtime.
4. New Stage-3 surfaces must justify themselves by increasing search power or reuse clarity.
5. No new lane is introduced in the first half of Stage 3.
6. No async runtime substrate lands before replay-stable bounded search exists.
7. ATP remains an audit lane, not the defining proving lane.

## 6. Stage-3 lane taxonomy

### Foundation lanes

- `lane.harness`
- `lane.repo_swe`

### Real-search primary lanes

- `lane.repo_swe`
- `lane.systems`

### Secondary confirmation lane

- `lane.scheduling`

### Audit lane

- `lane.atp`

### Deferred centrality

- `lane.research`

## 7. Stage-3 primitive thesis

Stage 3 should stay focused on the smallest high-leverage primitive program:

1. `OptimizationSubstrateV1`
2. `ExecutionPlanV1`
3. `EvaluatorPackV1` plus `BudgetEnvelope`
4. `DecisionLedgerV1`

Everything else should stay either:

- existing runtime truth
- compiled view
- or derived artifact

## 8. Stage-3 model-routing doctrine

Stage 3 should assume:

- `gpt-5.4-mini` = default low-cost high-quality search worker
- `gpt-5.4-nano` = bulk triage / filtering / low-risk repeated worker
- stronger tier = tranche planning, hard arbitration, and exceptional ambiguity only

This routing doctrine is local to Stage-3 DARWIN/BreadBoard work and does not imply a repo-wide default rewrite.

## 9. Build-now / later / reject

### Build now

- optimization substrate uptake
- selectively consumable `ExecutionPlan`
- stronger `EvaluatorPack` semantics
- budget and cost telemetry hooks
- minimal canonical `DecisionLedger`
- bounded `mini` / `nano` real-search canaries

### Build later

- adaptive budget allocation
- broader topology sampling
- broader transfer families
- research-lane centrality
- async shadow programs

### Reject for early Stage 3

- new runtime truth
- giant topology DSL
- archive as canonical truth
- ATP-first roadmap
- public superiority package
- uncontrolled multi-lane inference campaigns

## 10. Immediate next step

The next implementation move is **not** broad coding.

The next move is:

1. freeze Stage-3 doctrine
2. write the initial ADR set
3. define tranche-0 and tranche-1 execution work against those ADRs

