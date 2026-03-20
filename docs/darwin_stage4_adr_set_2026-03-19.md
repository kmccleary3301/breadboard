# DARWIN Stage-4 ADR Set

Date: 2026-03-19
Status: proposed ADR set for Stage 4
References:
- `docs/darwin_stage4_doctrine_2026-03-19.md`
- `docs/darwin_stage4_execution_plan_2026-03-19.md`
- `docs/darwin_stage3_signoff_2026-03-19.md`
- `docs_tmp/DARWIN/BB_DARWIN_PHASE_4_PLANNER_RESPONSE.md`

## Purpose

This ADR set translates the Stage-4 planner response into the smallest implementation-grade decision set needed to begin Stage 4.

It does not authorize broad coding by itself. It defines the decisions that must govern the first implementation tranche.

## Global Stage-4 rules

1. Stage-3 closeout on `main` remains frozen.
2. Stage 4 is a new program, not a continuation of Stage-3 closeout.
3. Runtime truth remains singular and BreadBoard-owned.
4. Only live-provider arms count toward Stage-4 power claims.
5. Stage-4 primitives should increase compounding power, not packaging density.
6. Repo_SWE and Systems are the primary proving lanes.
7. Harness remains the low-cost watchdog lane.
8. ATP remains audit-only.
9. Research remains deferred from centrality early in Stage 4.

---

## ADR-01 — Stage-4 thesis, boundary, and lane matrix

### Status

Proposed.

### Problem

Stage 3 is complete. Without a new doctrine and lane matrix, Stage 4 will drift into local continuation rather than deliberate program design.

### Decision

Adopt a new Stage-4 doctrine with:

- a clean program reset
- explicit live vs scaffold claim boundary
- fixed early lane taxonomy
- explicit non-goals
- explicit routing and spend doctrine
- explicit blast-radius and claim-escalation rules

### Lane matrix

- primary: `lane.repo_swe`, `lane.systems`
- watchdog/control: `lane.harness`
- confirmation: `lane.scheduling`
- audit: `lane.atp`
- deferred centrality: `lane.research`

### Non-goals

- not a Stage-3 continuation
- not a broad lane-expansion program
- not an ATP roadmap reset
- not an async-first search program

### Exit criteria

- doctrine is written and linked
- lane roles are fixed for early Stage 4
- live/scaffold claim eligibility is explicit

---

## ADR-02 — Live vs scaffold execution modes and claim eligibility

### Status

Proposed.

### Problem

Stage 3 introduced route-aware inference and telemetry scaffolding, but local scaffold-mode runs and live-provider runs cannot be treated as equivalent evidence.

### Decision

Stage 4 should define two execution modes:

- `execution_mode=scaffold`
- `execution_mode=live`

Only `execution_mode=live` campaigns count toward Stage-4 power claims.

### Required live fields

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

### Non-goals

- not a prohibition on scaffold-mode canaries
- not a public economics package

### Exit criteria

- live and scaffold are explicit in all Stage-4 campaign reporting
- claim-bearing summaries exclude scaffold-only arms

---

## ADR-03 — `ExecutionEnvelopeV2` consumption scope

### Status

Proposed.

### Problem

Stage-3 plan consumption proved only a narrow binding subset. Stage 4 needs deeper runtime-adjacent closure without creating a second runtime truth.

### Decision

Advance `ExecutionPlanV1` into `ExecutionEnvelopeV2` as the compiled execution closure for selected authorized consumers.

### Truth boundary

- authored config remains human input
- effective config remains compiled resolution
- `ExecutionEnvelopeV2` becomes the compiled execution closure for selected runtime-adjacent consumers
- runtime event log remains singular runtime truth

### Authorized early scope

- topology family and parameter binding
- workspace and tool binding inspection
- policy reference binding
- budget/environment binding lookup
- support-envelope and mutation-bound validation

### Not authorized early

- replacing runtime truth
- arbitrary graph authoring
- giant topology grammar
- broad runtime rewrites

### Blast radius

- `BR2` emitted-only or explanatory use
- `BR4` once runtime-adjacent consumers rely on it

### Exit criteria

- limited runtime consumption is proven without replay/parity regressions
- execution closure is materially clearer than current baggy config behavior

---

## ADR-04 — `SearchPolicyV1` and campaign-class doctrine

### Status

Proposed.

### Problem

Stage 3 can report operator and topology EV, but search policy remains too static and manual to support real compounding.

### Decision

Introduce `SearchPolicyV1` as a typed DARWIN-side policy object that governs:

- operator and topology family priors
- campaign-class selection
- arm allocation
- budget use by route class
- abort logic

### Campaign classes

- `C0 Scout`
- `C1 Discovery`
- `C2 Validation`
- `C3 Promotion`

### Non-goals

- not a black-box auto-tuning layer
- not meta-evolution over everything
- not opaque heuristic soup

### Exit criteria

- search policy can drive a bounded pilot on the primary lanes
- campaign classes are operationally distinct
- search allocation is more than manual tranche budgeting

---

## ADR-05 — `ComponentFamilyV1` lifecycle and promotion/transfer rules

### Status

Proposed.

### Problem

Stage 3 proved a first family and created registry/scorecard/memo surfaces, but those surfaces will become ceremonial unless family state becomes operational.

### Decision

Introduce `ComponentFamilyV1` as the operational family-state layer for Stage 4.

### Minimal lifecycle states

- `candidate`
- `provisional`
- `promoted`
- `retained`
- `stable`
- `lane_local`
- `deprecated`

### Rules

- replay-backed promotion remains mandatory
- transfer eligibility reads family state
- search policy can consume family state
- scorecards and memos remain derived-only

### Non-goals

- not a giant family taxonomy
- not every prompt fragment becoming a family
- not a new evidence family

### Exit criteria

- family state changes drive promotion and transfer decisions
- family state is operationally consumed by search or transfer logic

---

## ADR-06 — Lane matrix and ATP/Research boundaries

### Status

Proposed.

### Problem

Without a hard lane matrix, Stage 4 will drift into breadth before it earns compounding power.

### Decision

Keep the early Stage-4 lane matrix fixed:

- primary: Repo_SWE, Systems
- watchdog/control: Harness
- confirmation: Scheduling
- audit: ATP
- deferred centrality: Research

### Why

- Repo_SWE and Systems are the only lanes with enough upside to justify Stage-4 investment
- Harness is the cheapest regression detector
- Scheduling is the best lower-noise confirmation lane
- ATP is valuable but dangerous as a roadmap center
- Research remains too contamination-prone for early centrality

### Exit criteria

- Stage-4 tranche plans adhere to this matrix
- any lane expansion requires a later explicit decision

---

## ADR-07 — Provider-backed `BudgetEnvelope` and matched-budget rules

### Status

Proposed.

### Problem

Stage-3 budget semantics are not yet strong enough for live-provider compounding claims.

### Decision

Upgrade the current budget/evaluator path so every live claim-bearing arm carries provider-backed budget telemetry and stricter matched-budget comparison rules.

### Required comparison dimensions

A claim-bearing comparison should be invalid unless the comparison contract is explicit across:

- lane id
- evaluator-pack version
- support-envelope digest
- route class
- execution mode
- budget class
- comparison class
- task slice or workspace snapshot
- topology family when relevant
- control reserve policy

### Non-goals

- not a generic finance subsystem
- not public economics packaging
- not a repo-wide routing rewrite

### Exit criteria

- live-provider telemetry is present on claim-bearing arms
- matched-budget comparisons are materially tighter than Stage 3
- invalid comparisons are blocked or surfaced automatically
