# Optimization V2 Suites And Target Families

Optimization V2 starts where V1 and V1.5 stop.

V1 completed the optimization substrate:

- typed targets and mutable loci
- datasets, truth packages, diagnostics, and wrongness
- reflective backend and promotion gates
- runtime-context and support-envelope discipline

V1.5 then completed evidence and integration:

- benchmark manifests
- paired candidate comparison
- benchmark run results
- benchmark-aware promotion evidence
- fixed-methodology backend comparison

Optimization V2 does **not** replace those layers.

It adds the next organization layer that V1.5 still left implicit:

- reusable evaluation suites
- reusable objective suites
- explicit target families
- explicit bounded search spaces

These are still non-kernel optimization-layer artifacts.

## New V2 records

The first V2 tranche adds these records in `agentic_coder_prototype/optimize/suites.py`:

- `EvaluationSuiteManifest`
- `ObjectiveSuiteManifest`
- `ObjectiveBreakdownResult`
- `TargetFamilyManifest`
- `SearchSpaceManifest`

These records are intentionally narrow.

They exist to bind real harness/config/prompt optimization lanes to declared methodology, not to create a second optimizer ontology.

## EvaluationSuiteManifest

`EvaluationSuiteManifest` defines what one evaluation stack means for one target family.

It records:

- suite identity and kind
- evaluator ordering
- split visibility policy
- stochasticity class
- rerun policy
- capture requirements
- adjudication requirements
- comparison protocol defaults
- artifact requirements

This is the durable answer to:

- which evaluator stack is in force
- which splits are mutation-visible or hidden
- which captures are required before a comparison is considered meaningful

## ObjectiveSuiteManifest

`ObjectiveSuiteManifest` derives optimization channels from evaluation outputs.

It records:

- objective channels
- penalty channels
- aggregation rules
- uncertainty policy
- frontier dimensions
- promotion annotations
- visibility annotations

This keeps the BreadBoard doctrine intact:

- evaluation remains primary
- objective scoring is derived
- promotion does not collapse into one scalar reward

## ObjectiveBreakdownResult

`ObjectiveBreakdownResult` is the inspectable scoring artifact for one candidate under one objective suite.

It records:

- per-sample component values
- per-bucket component values
- aggregate objective values
- uncertainty notes
- blocked or undefined components
- supporting artifact refs

This is the first V2 artifact that makes suite-backed scoring visible without pretending it is a kernel truth object.

## TargetFamilyManifest

`TargetFamilyManifest` defines what kind of thing is being optimized.

It records:

- family identity and kind
- family scope
- target membership
- mutable loci set
- suite bindings
- review class
- runtime-context assumptions
- promotion class

This is the contract for a real optimization family rather than a one-off benchmark pack.

## SearchSpaceManifest

`SearchSpaceManifest` defines what search is actually allowed for one target family.

It records:

- allowed loci
- allowed mutation kinds per locus
- admissible value domains
- semantic constraints
- invariants
- unsafe expansion notes

This is the bounded-search counterpart to the family manifest.

The first tranche rejects:

- unknown loci in constraints
- missing mutation declarations
- missing value-domain declarations

That keeps search-space growth explicit instead of ambient.

## First live families

The initial V2 tranche now binds three real proving families through the new suite/family layer:

- support and execution heuristics in `build_support_execution_benchmark_example`
- tool guidance and tool-pack wording in `build_tool_guidance_benchmark_example`
- bounded coding-harness overlays in `build_coding_overlay_benchmark_example`

Each example now includes:

- `evaluation_suite`
- `objective_suite`
- `target_family`
- `search_space`
- `objective_breakdown_result`

The benchmark manifest and benchmark result are also annotated with:

- `evaluation_suite_id`
- `objective_suite_id`
- `target_family_id`
- `search_space_id`

The promotion-readiness slice now includes:

- `objective_breakdown_result_id`

This is deliberate.

The first V2 tranche is not a new optimizer yet. It is the point where three real optimization lanes become suite-bound and family-bound rather than remaining only benchmark-bound.

## Backend-only staged optimizer

The next V2 tranche adds a backend-private staged optimizer in `agentic_coder_prototype/optimize/backend.py`:

- `StagePlanStep`
- `StagedOptimizerRequest`
- `StagedOptimizer`
- `run_staged_optimizer(...)`

This backend is deliberately **not** a new public architecture center.

It operates over:

- an existing reflective backend request
- one declared evaluation suite
- one declared objective suite
- one declared target family
- one declared search space

The stage plan stays small and deterministic.

Each stage declares:

- allowed loci
- primary objective channels
- allowed split visibilities

Hidden holds remain out of bounds for mutation-time optimization.

They stay comparison and promotion evidence only.

The canonical comparison proof is `build_staged_backend_comparison_example`, which compares the staged optimizer against the reflective backend on the same three live family manifests.

## Family-level promotion evidence

V2 also extends the existing promotion layer rather than creating a second one.

`PromotionEvidenceSummary` now carries suite and family scope directly:

- evaluation suite ids
- objective suite ids
- target family ids
- search space ids
- objective breakdown result ids
- family bucket coverage
- hidden-hold and regression bucket coverage
- applicability scope
- family risk summary
- review class
- objective breakdown status

The family-aware gate is `evaluate_family_promotion_gate(...)`.

This keeps the promotion doctrine intact:

- benchmark comparison still matters
- family scope still matters
- review class still matters
- objective breakdown completeness still matters

Support-heavy families remain explicit review cases.

Non-review-heavy families can pass with `win` or `non_inferior` evidence if hidden-hold and regression coverage are real.

## Optional verifier-augmented follow-on

The current V2 follow-on experiment stays intentionally narrow.

It does **not** add a new backend family, campaign loop, or public verifier workflow.

Instead it adds one suite-driven verifier-augmented refinement example:

- `VerifierAugmentedExperimentResult`
- `build_coding_overlay_verifier_experiment_example`

This experiment is tied directly to the live `coding_overlay` family and reuses its declared:

- evaluation suite
- objective suite
- target family
- search space

The refinement is narrow:

- it starts from the existing coding-overlay child candidate
- it applies one verifier-guided edit-policy refinement
- it compares that refinement on the existing manifest
- it records the result as a non-kernel experiment artifact

This keeps the boundary clear:

- verifier experimentation is allowed
- family/search-space discipline remains primary
- no DARWIN campaign state is introduced
- no archive, island, or genealogy ontology is introduced

The experiment exists to prove that verifier-shaped follow-ons can remain subordinate to the declared V2 methodology instead of becoming a second optimizer architecture.

## Why this layer exists

V1.5 could tell us:

- which candidate beat which other candidate
- under which benchmark manifest
- on which hidden-hold and regression slices

But it still left these questions under-modeled:

- which evaluator stack should be reused for a family of related targets
- how evaluation outputs should become objective channels
- what bounded search is admissible for a target family
- how to express family-level review and promotion class

V2 starts making those answers explicit.

## What this tranche does not do

This first V2 tranche does not introduce:

- DARWIN campaign state
- archive, island, or genealogy ontology
- online self-tuning
- a public reward DSL
- a public optimizer workflow product surface
- a canonical staged optimizer architecture

Those later phases are still deferred until suite and family contracts are real across multiple lanes.

## Reading order

For the smallest end-to-end V2 walkthrough, read:

1. `EvaluationSuiteManifest`
2. `ObjectiveSuiteManifest`
3. `ObjectiveBreakdownResult`
4. `TargetFamilyManifest`
5. `SearchSpaceManifest`
6. `build_support_execution_benchmark_example`
7. `build_tool_guidance_benchmark_example`
8. `build_coding_overlay_benchmark_example`
9. `build_coding_overlay_verifier_experiment_example`

That sequence shows the first real family/suite bindings on top of the completed V1.5 evidence stack.
