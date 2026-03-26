# DARWIN Stage-5 ADR Set

Date: 2026-03-20
Status: proposed ADR set for Stage 5
References:
- `docs/darwin_stage5_doctrine_2026-03-20.md`
- `docs/darwin_stage5_execution_plan_2026-03-20.md`
- `docs/darwin_stage4_signoff_2026-03-20.md`
- `docs_tmp/DARWIN/phase_5/BB_DARWIN_PHASE_5_PLANNER_RESPONSE.md`

## Purpose

This ADR set translates the Stage-5 planner response into the smallest implementation-grade decision set needed to begin Stage 5.

It does not authorize broad coding by itself. It defines the decisions that must govern the first implementation tranche.

## Global Stage-5 rules

1. Stage-4 closeout on `main` remains frozen.
2. Stage 5 is a new program, not a continuation of Stage-4 closeout.
3. Runtime truth remains singular and BreadBoard-owned.
4. Only live-provider arms count toward Stage-5 power claims.
5. Stage-5 primitives should increase measurable compounding rate, not packaging density.
6. Repo_SWE and Systems are the primary proving lanes.
7. Harness remains the low-cost watchdog/control lane.
8. ATP remains audit-only.
9. Research remains deferred from centrality early in Stage 5.

---

## ADR-01 — Stage-5 thesis, success metrics, and anti-goals

### Status

Proposed.

### Problem

Stage 4 is complete. Without a new doctrine and success metric set, Stage 5 will drift into “more campaigns” rather than proving scalable compounding.

### Decision

Adopt a new Stage-5 doctrine with:

- a clean program reset
- explicit compounding-rate thesis
- explicit anti-goals
- explicit lane-class matrix
- explicit OpenRouter economics stance
- explicit first-slice boundaries

### Success criteria

Stage 5 must eventually show:

- warm-start family-aware campaigns beat matched lockout or cold-start controls
- family reuse measurably improves later search efficiency, promotion velocity, retained transfer, or cost efficiency
- family state becomes operationally consumed by search allocation

### Non-goals

- not a Stage-4 continuation
- not campaign throughput theater
- not family inventory theater
- not an async-first program

### Exit criteria

- doctrine is written and linked
- compounding protocol is explicit
- Stage-5 anti-goals are explicit

---

## ADR-02 — Live/scaffold and provider-economics truth

### Status

Proposed.

### Problem

Stage 4 improved live/scaffold truth, but OpenRouter/OpenAI route behavior and mixed cost truth still limit Stage-5 economic claims.

### Decision

Stage 5 should keep two execution modes:

- `execution_mode=scaffold`
- `execution_mode=live`

Only `execution_mode=live` campaigns count toward Stage-5 power claims.

### Required live fields

Every claim-bearing live arm should carry:

- route id
- provider origin
- provider model
- execution mode
- provider-backed token telemetry when available
- cached-input and cache-write fields when present
- effective cost and cost source
- fallback reason when fallback occurs
- budget class
- comparison class
- evaluator-pack version
- support-envelope and comparison-envelope refs

### Non-goals

- not a public economics package
- not a promise that OpenRouter-first economics are already clean

### Exit criteria

- live and scaffold remain explicit in all Stage-5 reporting
- provider origin and fallback reasons are canonical
- mixed-provider runs are segmented rather than blurred

---

## ADR-03 — `CompoundingCaseV1` and family-aware comparison protocol

### Status

Proposed.

### Problem

Stage 4 proved bounded compounding, but the key Stage-5 question — whether promoted families make later search better — is still mostly implicit.

### Decision

Introduce `CompoundingCaseV1` as a small DARWIN-side typed record representing:

- lane
- campaign class
- comparison mode
- family context
- evaluator-pack ref
- budget envelope ref
- route/provider envelope ref
- outcome deltas
- compounding conclusion

### Comparison modes

- `cold_start`
- `warm_start`
- `family_lockout`
- `transfer_probe`
- `composition_probe`

### Non-goals

- not a new top-level primitive family
- not a second ledger
- not a memo class

### Exit criteria

- at least one warm-start vs lockout protocol run is cleanly expressible
- `CompoundingCaseV1` can be emitted without changing runtime truth

---

## ADR-04 — `SearchPolicyV2`

### Status

Proposed.

### Problem

Stage-4 search policy is still too pilot-oriented and too lane-scripted to support scalable family-aware compounding.

### Decision

Introduce `SearchPolicyV2` as a typed DARWIN-side policy object that governs:

- operator priors
- family priors
- route priors
- comparison-mode quotas
- campaign-class chooser
- abort thresholds

### Non-goals

- not opaque heuristic soup
- not a giant learned controller
- not meta-evolution over everything

### Exit criteria

- search policy can drive a bounded Repo_SWE warm-start vs lockout pilot
- family state influences allocation in a typed and inspectable way

---

## ADR-05 — `ComponentFamilyV2` lifecycle and retained-transfer policy

### Status

Proposed.

### Problem

Stage-4 family state is strong enough for bounded proof, but still too report-oriented for scalable compounding.

### Decision

Introduce `ComponentFamilyV2` as the operational family-state layer for Stage 5.

### Minimal lifecycle states

- `observed`
- `provisional`
- `promoted`
- `retained`
- `stable`
- `lane_local`
- `withheld`
- `deprecated`

### Additional semantics

- family scope/support-envelope semantics
- transfer eligibility state
- family reuse metrics
- composition eligibility as a boolean, not a major subsystem

### Non-goals

- not a giant family ontology
- not every prompt fragment becoming a family
- not a graph database

### Exit criteria

- family state changes can drive search allocation, promotion, and transfer eligibility
- retained-transfer policy is explicit and narrow

---

## ADR-06 — Lane classes and Research/ATP boundaries

### Status

Proposed.

### Problem

Stage 5 needs stronger confidence without losing the tight Stage-4 proving center.

### Decision

Adopt four lane classes:

- primary search lanes: `lane.repo_swe`, `lane.systems`
- confirmation lanes: `lane.harness`, `lane.scheduling`
- audit lane: `lane.atp`
- deferred consumer lane: `lane.research`

### Non-goals

- not a new domain-lane expansion
- not ATP re-centralization
- not early Research centrality

### Exit criteria

- Stage-5 proving order is explicit
- Research remains deferred until late consumer use is justified

---

## ADR-07 — Shared optimize-substrate uptake plan

### Status

Proposed.

### Problem

The optimize substrate now contains stronger search objects than some of the thinner Stage-4 live paths, but it is not yet fully operational in the compounding path.

### Decision

Stage 5 should selectively promote shared optimize-substrate objects into the operational search path, especially around:

- `OptimizationTarget`
- `MutableLocus`
- `SupportEnvelope`
- `MutationBounds`
- `CandidatePortfolio`
- promotion and gate results

### Boundary

- no new kernel truth
- no giant cross-cutting rewrite
- no replacement of BreadBoard runtime truth

### Exit criteria

- the first Stage-5 slice has an explicit uptake plan
- shared substrate ownership is clear before deeper family-aware search coding begins
