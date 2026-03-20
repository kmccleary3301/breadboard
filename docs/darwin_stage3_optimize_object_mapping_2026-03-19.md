# DARWIN Stage-3 Optimize Object Mapping

Date: 2026-03-19
Status: tranche-1 canary mapping
References:
- `docs/darwin_stage3_tranche1_slice_2026-03-19.md`
- `docs/darwin_stage3_tranche1_canary_status_2026-03-19.md`
- `breadboard_ext/darwin/stage3.py`

## Purpose

This note records the first explicit mapping between the existing optimize substrate and the Stage-3 DARWIN search vocabulary.

The point is to avoid inventing a second parallel DARWIN ontology.

## Mapping

### `OptimizationTarget`

Stage-3 meaning:

- the canonical search target for one bounded lane/task surface

Current tranche-1 usage:

- `lane.repo_swe`
- `lane.systems`

Stage-3 binding:

- search target identity
- baseline artifact reference
- lane/task metadata
- support envelope
- invariants

### `MutableLocus`

Stage-3 meaning:

- the smallest bounded mutable surface we are willing to search directly

Current tranche-1 loci:

#### `lane.repo_swe`

- `topology.params`
- `operator.family`
- `tool.scope`
- `policy.bundle`

#### `lane.systems`

- `topology.params`
- `operator.family`
- `policy.bundle`
- `evaluator.control`

These are deliberately coarse. They are intended to preserve interpretability and avoid prompt-fragment sprawl.

### `SupportEnvelope`

Stage-3 meaning:

- the explicit support and comparability boundary for a target

Current tranche-1 usage:

- allowed tools
- execution profile
- environment digest
- provider/model family
- budget class and replay assumptions in metadata

This is the main guard against hidden widening of search scope.

### `MutationBounds`

Stage-3 meaning:

- the explicit bound on how much a candidate may change

Current tranche-1 state:

- not yet runtime-enforced in the DARWIN canary path
- still the correct object for the next substrate-uptake code slice

### `CandidateBundle` / `MaterializedCandidate`

Stage-3 meaning:

- candidate-level carrier of bounded changes

Current tranche-1 state:

- still not the main DARWIN runtime path
- should remain subordinate to component-oriented reasoning

### `PromotionDecision` / `PromotionRecord`

Stage-3 meaning:

- component or candidate state transition under replay/evaluator constraints

Current tranche-1 state:

- decision truth still centered in DARWIN ledger surfaces
- optimize-side promotion objects are the right source vocabulary for further narrowing

## Non-goals

This mapping does not authorize:

- arbitrary mutation of every config field
- prompt-fragment component IDs
- archive-as-truth
- runtime-truth replacement

## Read

The first important conclusion is simple:

- the optimize substrate already provides the right bounded vocabulary for Stage 3
- DARWIN should consume that vocabulary rather than invent a competing one

