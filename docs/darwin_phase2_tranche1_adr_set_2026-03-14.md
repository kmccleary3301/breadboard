# DARWIN Phase-2 Tranche-1 ADR Set

Date: 2026-03-14
Status: proposed ADR set for tranche 1
References:
- `docs/darwin_phase2_tranche1_execution_plan_2026-03-14.md`
- `docs/darwin_phase2_handoff_2026-03-14.md`

## Purpose

This ADR set translates the tranche-1 execution plan into concrete architectural decisions for the first Phase-2 build tranche.

This set is intentionally narrow. It does not redesign DARWIN or BreadBoard globally. It defines the smallest semantic upgrades needed to start Phase 2 safely.

## Global tranche-1 rules

These rules apply to all ADRs in this document.

1. No new BreadBoard kernel-truth primitive lands in tranche 1.
2. New tranche-1 objects begin as emitted or reconstructed read-models whenever possible.
3. Runtime truth remains in existing BreadBoard runtime/event-log surfaces.
4. Control truth and evaluation truth remain in DARWIN extension layers.
5. Harness and Repo_SWE are primary proving lanes.
6. Scheduling is secondary confirmation.
7. ATP is audit-only secondary support, not a roadmap-defining lane.
8. Research and systems stay mostly out of tranche 1 except where reconstruction or later confirmation is required.

---

## ADR-01 — `ExecutionPlan` boundary

### Status

Proposed for tranche 1.

### Problem

Current run meaning is split across:

- authored config
- best-effort overrides
- team/topology/orchestration config
- runtime context
- emitted artifacts

That makes local reasoning harder than it should be.

### Decision

Introduce `ExecutionPlan` as a **compiled execution artifact**, not as a new runtime truth object.

`ExecutionPlan` should contain:

- digests of authored input and compiled config
- compiled execution graph
- tool/workspace/environment/budget bindings
- references to runtime truth surfaces, not copies of them

### Non-goals

- not a replacement for run context or event log
- not a new session state model
- not a new runtime authority
- not a general graph DSL

### Source surfaces

Compiles from:

- authored config
- validated overrides
- current team/topology/orchestration config
- known lane/runtime bindings

### Truth boundary

- canonical runtime truth remains BreadBoard runtime/event-log state
- `ExecutionPlan` is canonical only as the compiled explanation of intended runtime structure for one run

### Migration strategy

- shadow-emitted only in tranche 1
- dual-write with current run artifacts
- never runtime-consumed in tranche 1
- parity/replay checks must compare plan against actual runtime behavior

### Blast radius

`BR2` while emitted only.
`BR4` if runtime ever begins consuming it.

### Proving lanes

Primary: `lane.harness`
Secondary: `lane.repo_swe`, `lane.scheduling`

### Exit criteria for tranche 1

- emitted on all tranche-1 proving runs
- stable digests and structure
- explainability materially improved
- no claim-bearing runtime regressions

---

## ADR-02 — `PolicyPack` semantics

### Status

Proposed for tranche 1.

### Problem

Current policy truth is spread across:

- `PolicyBundle` V0
- registries
- operator declarations
- lane compatibility/support flags
- tool/memory allowances
- evaluator gate expectations

Much of it is named but semantically baggy.

### Decision

Introduce `PolicyPack` as a **strict compiled policy wrapper** over existing policy inputs.

`PolicyPack` should contain:

- resolved policy identity and digests
- operator eligibility and maturity refs
- lane compatibility/support markers
- memory/tool-policy refs
- budget-policy refs
- evaluator-gate refs where applicable

### Non-goals

- not a new authored config language
- not a second policy authoring system
- not a giant graph of nested sub-policies
- not the place for lane-specific heuristic storage

### Source surfaces

Compiles from:

- `CampaignSpec`
- `PolicyBundle` V0
- mutation/operator registry
- lane registry and support metadata
- existing budget and policy declarations

### Truth boundary

- authored policy input remains current V0 surfaces in tranche 1
- `PolicyPack` becomes the strict compiled policy view used for explanation and later decision tightening

### Migration strategy

- emitted first as `effective_policy`
- compare old-vs-new policy interpretation on proving lanes
- allow old authored inputs to remain authoritative until semantic equivalence is established

### Blast radius

`BR2` while emitted only.
`BR4` if runtime policy consumption changes.

### Proving lanes

Primary: `lane.harness`
Secondary: `lane.repo_swe`
Audit: `lane.atp`

### Exit criteria for tranche 1

- explains current proving runs more clearly than raw V0 inputs
- compatibility/support markers are explicit and stable
- no hidden lane exceptions are smuggled into the compiler

---

## ADR-03 — `EvaluatorPack` semantics

### Status

Proposed for tranche 1.

### Problem

Current evaluator semantics are real, but still too split across:

- lane evaluator wrappers
- invalid-comparison handling
- metric family assumptions
- scorecard scripts
- claim-bearing emitter logic

This is enough for Phase 1, but too loose for Phase 2 transfer and component work.

### Decision

Introduce `EvaluatorPack` as a **strict typed wrapper** around evaluator semantics and control expectations.

`EvaluatorPack` should contain:

- evaluator identity
- metric family and score semantics
- invalid-comparison rules
- required controls / perturbations / negative-control expectations where relevant
- claim-bearing constraints and comparability notes

### Non-goals

- not a replacement for lane-specific evaluators
- not a generic scientific workflow engine
- not a public benchmark packaging system

### Source surfaces

Wraps:

- current lane evaluator adapters
- invalid-comparison logic
- metric normalization assumptions
- claim-bearing emitter requirements

### Truth boundary

- lane evaluators remain execution-side evaluation mechanisms
- `EvaluatorPack` becomes the canonical DARWIN evaluation semantics view for claim-bearing and comparison paths

### Migration strategy

- start on `lane.harness` and `lane.repo_swe`
- emit pack and stricter evaluation metadata first
- allow claim-bearing emitters to depend on it only after proving-lane stability is demonstrated

### Blast radius

`BR3` by default.
Potentially `BR4` if evaluator semantics indirectly alter runtime decisions.

### Proving lanes

Primary: `lane.harness`, `lane.repo_swe`
Audit: `lane.atp`
Confirmation: `lane.scheduling`

### Exit criteria for tranche 1

- known-valid and known-invalid comparison cases are represented correctly
- claim-bearing evaluator paths can consume the pack without semantic regressions
- metric semantics become more locally inspectable

---

## ADR-04 — `EvolutionLedger` and minimal component ontology

### Status

Proposed for tranche 1.

### Problem

Current lineage and decision truth is fragmented across:

- candidate artifacts
- archive snapshots
- promotion decisions
- transfer ledgers
- dossier/evidence references

That is enough for Phase 1, but not enough for clean component-centric Phase 2 work.

### Decision

Introduce `EvolutionLedger` as DARWIN’s **decision and component-lineage truth**.

It should contain:

- `component_ref`
- `decision_record`
- candidate-to-component bindings
- references to replay/evidence/claim artifacts
- promotion / rollback / transfer / deprecation decisions

### Minimal component ontology

Exactly four component kinds in tranche 1:

- `topology`
- `policy`
- `operator`
- `evaluator_pack`

### Non-goals

- not a graph database project
- not runtime execution truth
- not component IDs for prompt snippets, single flags, or memory nodes
- not a canonical archive object separate from ledger truth

### Source surfaces

Reconstructs from:

- current candidate artifacts
- promotion history artifacts
- transfer ledgers
- canonical Phase-1 evidence and dossier refs

### Truth boundary

- runtime truth remains BreadBoard-side
- `EvolutionLedger` is DARWIN control/evidence decision truth only
- archive, scorecard, and dossier views remain derived-only surfaces

### Migration strategy

- read-model first
- reconstruct known Phase-1 cases
- dual-write decision records only after the reconstruction path is trustworthy

### Blast radius

`BR2` while reconstructed-only.
`BR3` once used by control/evidence emitters.

### Proving lanes

Primary: `lane.repo_swe`
Secondary: `lane.scheduling`
Audit: `lane.atp`
Special reconstruction case: existing harness→research transfer path

### Exit criteria for tranche 1

- representative scheduling promotion history reconstructs cleanly
- representative transfer case reconstructs cleanly
- component ontology remains small and stable
- archive is still derived, not promoted to canonical truth

---

## ADR-05 — Config compiler and override policy

### Status

Proposed for tranche 1.

### Problem

Current best-effort dotted-path overrides are convenient for debugging and too loose for claim-bearing semantics.

### Decision

Adopt a four-layer config model:

1. `IntentConfig` — authored human input
2. `EffectiveConfig` — compiled and validated resolved config
3. `ExecutionPlan` — compiled execution representation
4. runtime truth — what actually happened

Claim-bearing flows must:

- compile and emit `EffectiveConfig`
- validate override usage against an explicit allowlist
- fail closed on unknown or unsupported override paths

### Non-goals

- not a new config DSL
- not arbitrary user-authored expression logic
- not freeform runtime branching in config

### Allowed

- declarative references
- validated presets
- explicit whitelisted overrides
- compiler-emitted diffs and digests

### Banned for claim-bearing paths

- arbitrary expression logic
- implicit environment-dependent config behavior
- unbounded dotted-path mutation
- imperative lane-specific scripts hidden in config

### Blast radius

`BR2` while emitted only.
`BR4` if runtime semantics change because of compiled-config consumption.

### Proving lanes

Primary: `lane.harness`
Secondary: `lane.repo_swe`

### Exit criteria for tranche 1

- `EffectiveConfig` is emitted on proving runs
- claim-bearing overrides are explicitly validated
- local reasoning about run configuration is materially improved

---

## ADR-06 — Blast-radius and replay gate policy

### Status

Proposed for tranche 1.

### Problem

Current planning language about risk is directionally good, but tranche 1 needs an explicit operational gate for approving changes to primitives and compiled surfaces.

### Decision

Use the following blast-radius classes for tranche-1 work:

- `BR0`: docs / reporting / derived read-model only
- `BR1`: additive emitters / validators
- `BR2`: emitted compiler outputs not consumed by runtime
- `BR3`: evaluation / claim / decision semantics
- `BR4`: runtime-adjacent behavior changes
- `BR5`: parity-kernel changes

Tranche 1 must stay concentrated in `BR1`–`BR3`, with runtime behavior preserved.

### Mandatory replay/parity conditions

Replay/parity confirmation is mandatory for any change touching:

- runtime consumption of compiled plans or policies
- topology execution semantics
- tool availability semantics
- provider request shaping
- permission phrasing that affects execution behavior
- claim-bearing evaluation semantics

### Non-goals

- not a full governance bureaucracy
- not a blocker for read-model emission work

### Approval rule

- `BR0`–`BR1`: normal review
- `BR2`: architecture review + shadow proof
- `BR3`: evaluation/statistics signoff
- `BR4`: parity/replay signoff + claim freeze
- `BR5`: explicit tranche-level decision only

### Exit criteria for tranche 1

- all tranche-1 work packages are explicitly classed
- proving-run review includes BR classification
- any proposed runtime dependency expansion is blocked until tranche-1 gate review

---

## Tranche-1 proving summary

### Primary proving lanes

- `lane.harness`
- `lane.repo_swe`

### Secondary confirmation lane

- `lane.scheduling`

### Audit-only secondary lane

- `lane.atp`

### Mostly out of tranche 1

- `lane.systems`
- `lane.research`

## Immediate next step after this ADR set

If this ADR set is accepted, the next work item should be:

- emit `effective_config` and `execution_plan` in shadow mode on the proving lanes

That is the correct first implementation move.
