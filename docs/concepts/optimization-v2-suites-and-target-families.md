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

## First live family: support and execution heuristics

The first V2 proving family is the existing support/execution lane from V1.5, now rebound through the new suite/family layer in `build_support_execution_benchmark_example`.

That example now includes:

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

The first V2 tranche is not a new optimizer yet. It is the point where a real optimization lane becomes suite-bound and family-bound rather than remaining only benchmark-bound.

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
- a staged optimizer backend

Those later phases are still deferred until suite and family contracts are real across multiple lanes.

## Reading order

For the smallest end-to-end V2 walkthrough, read:

1. `EvaluationSuiteManifest`
2. `ObjectiveSuiteManifest`
3. `ObjectiveBreakdownResult`
4. `TargetFamilyManifest`
5. `SearchSpaceManifest`
6. `build_support_execution_benchmark_example`
7. `build_support_execution_benchmark_example_payload`

That sequence shows the first real family/suite binding on top of the completed V1.5 evidence stack.
