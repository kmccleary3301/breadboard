# DARWIN Stage-3 Substrate Ownership Note

Date: 2026-03-19
Status: tranche-1 ownership freeze

## Canonical Stage-3 substrate objects

The following shared optimize-layer objects are now the canonical Stage-3 mutable-object vocabulary:

- `OptimizationTarget`
- `MutableLocus`
- `SupportEnvelope`
- `MutationBounds`
- `CandidateBundle`
- `MaterializedCandidate`

## Ownership

- shared substrate truth: `agentic_coder_prototype/optimize/*`
- DARWIN consumption and decision semantics: `breadboard_ext/darwin/*`
- lane-local adapters and commands: lane scripts and lane runners

## Consumed, not redefined

DARWIN consumes the optimize substrate for:

- target definition
- locus definition
- bounded-candidate validation
- materialized candidate explanation

DARWIN does not redefine a parallel mutation ontology.

## Still not authorized

- arbitrary config mutation
- prompt-fragment componentization
- runtime-truth replacement
- broad substrate use on every lane
