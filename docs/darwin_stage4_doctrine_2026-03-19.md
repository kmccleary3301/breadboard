# DARWIN Stage-4 Doctrine

Date: 2026-03-19
Status: proposed Stage-4 doctrine and execution entrypoint
References:
- `docs_tmp/DARWIN/BB_DARWIN_PHASE_4_PLANNER_RESPONSE.md`
- `docs/darwin_stage3_signoff_2026-03-19.md`
- `docs/darwin_stage3_completion_gate_2026-03-19.md`
- `docs/darwin_stage3_future_roadmap_handoff_2026-03-19.md`

## 1. Purpose

This document starts Stage 4 as a **new BreadBoard + DARWIN program**.

It does not reopen Stage 3. It defines the doctrine that should govern Stage-4 planning and implementation.

## 2. Stage-4 mission

The Stage-4 mission is:

> turn BreadBoard + DARWIN into the first live-provider, multi-family compounding program that repeatedly discovers, promotes, reuses, and conservatively transfers bounded agentic component families under strict runtime, evaluator, and replay discipline.

## 3. What Stage 4 is trying to do

Stage 4 exists to increase **real system power and compounding**, not merely interpretive polish.

That means:

- live-provider search with real routing and economics
- stronger runtime-adjacent execution closure
- adaptive search policy rather than mostly static search reporting
- more than one promoted component family
- more than one retained bounded transfer or equivalent reuse signal
- search behavior that improves because prior promotions are operationally consumed

## 4. What Stage 4 must not become

Stage 4 must not become:

- a stealth continuation of Stage-3 closeout work
- a campaign-scaling program without compounding
- a new artifact-packaging program
- an ATP-centered roadmap
- a broad lane-expansion program
- an async/distributed-first program
- a second runtime truth
- an archive-as-truth redesign
- a topology-DSL project

## 5. Global Stage-4 rules

1. Stage-3 closure on `main` remains frozen and must not be reopened.
2. Runtime truth remains singular and BreadBoard-owned.
3. Only `execution_mode=live` campaigns count toward Stage-4 power claims.
4. Scaffold-mode runs remain useful for substrate testing and artifact generation, not capability claims.
5. Search-policy, execution closure, and family lifecycle must become more operational before campaign scale widens.
6. Repo_SWE and Systems are the primary proving lanes.
7. Harness remains watchdog/control.
8. Scheduling remains secondary confirmation.
9. ATP remains audit-only.
10. Research remains deferred from centrality.
11. Any BR4 runtime-adjacent regression freezes claim escalation until replay/parity clears.

## 6. Stage-4 lane taxonomy

### Primary proving lanes

- `lane.repo_swe`
- `lane.systems`

### Watchdog/control lane

- `lane.harness`

### Secondary confirmation lane

- `lane.scheduling`

### Audit lane

- `lane.atp`

### Deferred centrality

- `lane.research`

## 7. Stage-4 primitive thesis

Stage 4 should stay focused on the smallest high-leverage primitive program:

1. `ExecutionEnvelopeV2`
2. `SearchPolicyV1`
3. `ComponentFamilyV1`

Supporting upgrades are authorized only as extensions of existing Stage-3 truth surfaces:

- provider-backed `BudgetEnvelope` upgrade inside the evaluator path
- small `DecisionLedger` extension
- small BreadBoard telemetry and execution-plan hooks

Everything else should stay either:

- existing runtime truth
- compiled view
- or derived artifact

## 8. Stage-4 model-routing doctrine

Stage 4 should assume:

- `gpt-5.4-mini` = default live search worker
- `gpt-5.4-nano` = bulk triage / filtering / cheap repeated worker
- stronger tier = exceptional ambiguity, hard synthesis, or arbitration only

This routing doctrine is local to Stage-4 DARWIN/BreadBoard work and does not imply a repo-wide default rewrite.

## 9. Live-economics doctrine

Stage 4 should treat model spend as a portfolio allocation problem.

Every claim-bearing live arm should carry:

- route id
- provider model
- execution mode
- provider-backed token telemetry when available
- effective cost and cost source
- budget class
- comparison class
- support-envelope digest
- evaluator-pack version

Matched-budget comparisons are invalid unless the comparison contract is explicit.

## 10. Build-now / later / reject

### Build now

- `ExecutionEnvelopeV2`
- provider-backed budget telemetry
- `SearchPolicyV1` pilot
- `ComponentFamilyV1` lifecycle and operational family state
- deeper live campaigns on `lane.repo_swe` and `lane.systems`
- harness watchdog extensions
- scheduling transfer-confirmation slice

### Build later

- family composition canary after at least two real promoted families exist
- broader bounded transfer matrix after second-family promotion
- fresher Repo_SWE slices after live economics stabilize
- broader search-policy adaptation after operator/topology EV becomes real

### Reject for early Stage 4

- new runtime truth
- archive as canonical truth
- giant topology DSL
- config mini-language
- ATP-first roadmap
- strong-tier default routing
- async/distributed leading strategy
- scaffold-mode results counted as power evidence
- paperwork-heavy component program

## 11. Immediate next step

The next implementation move is not broad coding.

The next move is:

1. freeze Stage-4 doctrine
2. write the initial ADR set
3. define the Stage-4 execution plan and tranche structure against those ADRs
