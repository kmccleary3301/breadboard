# DARWIN Phase-2 Tranche-1 Execution Plan

Date: 2026-03-14
Status: proposed execution plan
Scope: first build tranche after DARWIN Phase-1 closure

## 1. Purpose

This document converts the Phase-2 planner analysis into an execution-ready tranche-1 plan.

The tranche-1 goal is **not** to broaden DARWIN. It is to make BreadBoard + DARWIN easier to reason about, safer to evolve, and more capable of component-centric search without reopening Phase-1 foundations.

Tranche 1 therefore focuses on:

- compiled execution visibility,
- stricter policy and evaluator semantics,
- a minimal component/decision ledger,
- and proving those changes on a narrow lane set.

## 2. Tranche-1 mission

Phase-2 tranche 1 exists to establish the smallest correct primitive upgrade that makes Phase 2 buildable.

The tranche-1 mission is:

> emit compiled execution and policy truth, tighten evaluator semantics, and add minimal component/decision lineage, without introducing new BreadBoard kernel-truth primitives.

## 3. Tranche-1 boundary

### Must do

- emit `effective_config`
- emit `execution_plan`
- emit `effective_policy`
- wrap evaluators into `EvaluatorPack`
- add minimal `EvolutionLedger` records
- reconstruct selected Phase-1 known cases through the new emitted surfaces

### Must not do

- no new kernel-truth primitive in BreadBoard core
- no async runtime dependency
- no reopening of Phase-1 contract scaffolding
- no reopening of six-lane bootstrap
- no C-Trees promotion work
- no broad lane expansion
- no systems-lane-first primitive proving
- no ATP-first primitive proving
- no publication-grade comparative work

## 4. Working primitive model

Tranche 1 assumes exactly four primitive families as the working compression target:

1. `ExecutionPlan`
2. `PolicyPack`
3. `EvaluatorPack`
4. `EvolutionLedger`

These are working targets for tranche-1 execution, not permanent truths beyond review.

### Interpretation

- `ExecutionPlan` is a compiled representation, not a new runtime truth
- `PolicyPack` is a stricter wrapper over current policy surfaces
- `EvaluatorPack` is a stricter wrapper over evaluator semantics and invalid-comparison rules
- `EvolutionLedger` is DARWIN decision truth for components, promotions, rollbacks, and transfers

## 5. Execution epics

### EPIC A — Compiled execution surfaces

Objective:
- make runs locally understandable without changing runtime truth

Includes:
- `effective_config`
- `execution_plan`
- digests / inspectability output
- diff views from authored input

### EPIC B — Policy and evaluator tightening

Objective:
- tighten semantic closure on policy and evaluator behavior without reopening Phase-1 contracts

Includes:
- `effective_policy`
- `PolicyPack`
- `EvaluatorPack`
- invalid-comparison fields and control requirements

### EPIC C — Minimal component / decision lineage

Objective:
- move from candidate-only truth toward minimal component-centric DARWIN

Includes:
- `component_ref`
- `decision_record`
- reconstructed promotion / transfer / archive-derived views

### EPIC D — Proving runs and tranche gate

Objective:
- prove the new emitted surfaces and wrappers on the right lanes before any runtime dependency shift

Includes:
- harness proving runs
- repo_swe proving runs
- scheduling confirmation runs
- ATP audit-only confirmation
- tranche-1 hard-gate review

## 6. ADR candidates

The following ADRs should be written before or during tranche 1.

### ADR-01 — `ExecutionPlan` boundary

Answer:
- what it contains
- what it compiles from
- what remains runtime truth instead
- what must stay derived only

### ADR-02 — `PolicyPack` semantics

Answer:
- which current policy surfaces are wrapped
- what remains authored input vs compiled output
- how operator/memory/tool-policy semantics are represented

### ADR-03 — `EvaluatorPack` semantics

Answer:
- evaluator identity
- metric semantics
- invalid-comparison rules
- control-pack expectations
- claim-bearing requirements

### ADR-04 — `EvolutionLedger` and minimal component ontology

Answer:
- `component_ref` kinds
- `decision_record` kinds
- what is canonical vs derived
- how promotion / rollback / transfer are represented

### ADR-05 — Config compiler and override policy

Answer:
- authored `IntentConfig`
- `EffectiveConfig`
- allowed override set
- banned override patterns for claim-bearing flows

### ADR-06 — Blast-radius and replay gates

Answer:
- BR classes
- required replay/parity checks by class
- when runtime consumption changes are allowed

## 7. Exact work packages

### WP-01 — Emit `effective_config`

Deliverables:
- emitted `effective_config` artifact per tranche-1 run
- digest and diff against authored config
- validation for allowed claim-bearing override paths

Success test:
- known tranche-1 runs emit stable compiled config without changing runtime behavior

### WP-02 — Emit `execution_plan`

Deliverables:
- compiled execution graph per run
- resolved workspace/tool/budget/environment bindings
- topology/team/orchestration projection

Success test:
- shadow `execution_plan` matches observed runtime structure on proving lanes

### WP-03 — Emit `effective_policy`

Deliverables:
- strict compiled policy artifact
- lane compatibility and support markers
- operator and memory/tool-policy refs

Success test:
- policy diffs explain current tranche-1 proving runs more clearly than raw authored input

### WP-04 — Harness `EvaluatorPack`

Deliverables:
- wrapped harness evaluator semantics
- invalid-comparison conditions
- control-pack expectations
- stricter evaluation record path

Success test:
- harness claim-bearing path can depend on `EvaluatorPack` without semantic regressions

### WP-05 — Repo_SWE `EvaluatorPack`

Deliverables:
- wrapped repo_swe evaluator semantics
- workspace-sensitive invalid-comparison handling
- metric family normalization

Success test:
- repo_swe comparisons become more locally auditable and survive existing known-good cases

### WP-06 — Add `component_ref` and `decision_record`

Deliverables:
- minimal component ontology
- decision records for promotion / rollback / transfer / deprecation
- ledger reconstruction path from existing Phase-1 artifacts

Success test:
- a representative scheduling promotion history and a harness→research transfer case can be reconstructed from the ledger outputs

### WP-07 — Reconstruct known Phase-1 cases

Cases to reconstruct:
- promoted scheduling candidate history
- harness→research transfer case
- representative repo_swe promotion/search case

Success test:
- reconstruction is stable, comprehensible, and consistent with existing Phase-1 canonical artifacts

### WP-08 — Tranche-1 hard-gate review

Deliverables:
- tranche-1 status memo
- artifact completeness checklist
- replay/parity notes
- explicit go/no-go on runtime dependency expansion

Success test:
- all tranche-1 gate conditions are met and documented

## 8. Proving-lane matrix

| Primitive family  | Primary lane | Secondary lane | Audit / confirmation lane | Keep mostly out |
| --- | --- | --- | --- | --- |
| `ExecutionPlan` | harness | repo_swe, scheduling | ATP | research, systems |
| `PolicyPack` | harness | repo_swe | ATP | research |
| `EvaluatorPack` | harness | repo_swe | ATP | research |
| `EvolutionLedger` | repo_swe | scheduling | ATP | systems in tranche 1 |

### Lane roles

- **harness**: cheapest and cleanest primary lane for compiled surfaces and evaluator tightening
- **repo_swe**: richest lane for artifact/workspace pressure and decision/lineage pressure
- **scheduling**: deterministic secondary lane for promotion / rollback reconstruction
- **ATP**: audit-only secondary lane for evaluator rigor, invalid comparison, and repair-accounting pressure
- **systems**: defer until tranche 2
- **research**: keep out except for reconstruction of the existing harness→research transfer case

## 9. Minimal component ontology

Tranche 1 should use exactly four first-class component kinds:

- `topology`
- `policy`
- `operator`
- `evaluator_pack`

Intentionally not first-class in tranche 1:

- prompt fragments
- individual topology edges
- single thresholds
- single tool flags
- memory nodes / summaries
- whole lanes
- whole campaigns
- whole candidates as the only unit of meaning

Candidate composition should move toward:

- `topology_component_id`
- `policy_component_id`
- `operator_component_ids[]`
- `evaluator_pack_id`
- `artifact_refs[]`
- `parent_candidate_ids[]`

## 10. “Do first / defer / kill now” board

### Do first

- `effective_config`
- `execution_plan`
- `effective_policy`
- harness `EvaluatorPack`
- repo_swe `EvaluatorPack`
- minimal `EvolutionLedger`
- reconstruction of known Phase-1 cases

### Defer

- runtime consumption of `execution_plan`
- systems lane as a primary proving lane
- broad transfer families
- async shadow
- archive redesign beyond ledger-derived views
- C-Trees promotion work
- publication-grade comparative outputs

### Kill now

- standalone `Archive V2` as canonical truth
- standalone `ArtifactCapsule`
- standalone `WorkspaceCapsule`
- standalone `CausalReplayGraph`
- config-language expansion
- ATP-first primitive proving
- runtime truth replacement in tranche 1

## 11. Tranche-1 hard gate

Tranche 1 is successful only if all of the following are true:

1. Harness and Repo_SWE both emit:
   - `effective_config`
   - `execution_plan`
   - `effective_policy`
   - `evaluator_pack`
   - `evolution_ledger` records
2. Those runs do not regress claim-bearing replay / parity expectations.
3. One existing scheduling promotion history is reconstructable from `EvolutionLedger` outputs.
4. One existing harness→research transfer case is reconstructable as a typed transfer path.
5. No new BreadBoard kernel-truth primitive was introduced.

## 12. Initial issue-map recommendation

### BreadBoard / shared-adapter work

- compiler emission for `effective_config`
- compiler emission for `execution_plan`
- inspectability and diff tooling for compiled outputs

### DARWIN control/eval work

- `PolicyPack` wrapper/compiler
- `EvaluatorPack` wrapper/compiler
- stricter evaluation-record semantics for claim-bearing paths

### DARWIN lineage work

- `component_ref`
- `decision_record`
- `EvolutionLedger` reconstruction logic
- derived archive / promotion / transfer views

### Tranche proving work

- harness tranche-1 proving bundle
- repo_swe tranche-1 proving bundle
- scheduling reconstruction confirmation
- ATP audit-only confirmation

## 13. Recommended next document after this one

If tranche 1 is accepted, the immediate next document should be:

- `docs/darwin_phase2_tranche1_adr_set_2026-03-14.md`

That ADR set should lock the exact semantics of:

- `ExecutionPlan`
- `PolicyPack`
- `EvaluatorPack`
- `EvolutionLedger`
- config compiler / override policy
- blast-radius / replay gate policy

## 14. Read of the planner follow-up

This tranche-1 plan intentionally adopts the narrower follow-up planner response rather than the broader original Phase-2 memo.

The key tranche-1 interpretation is:

- fewer stronger primitives,
- emitted and reconstructed truth before runtime dependence,
- harness and repo_swe as primary proving lanes,
- ATP as an evaluator-rigor pressure lane rather than a roadmap-defining lane.

That is the safest high-ceiling way to begin Phase 2.
