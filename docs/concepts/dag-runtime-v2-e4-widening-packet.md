# DAG Runtime V2 E4 Widening Packet

DAG Runtime V2 remains intentionally narrow.

It does **not** add:

- a second public search noun family
- async scheduler modes
- a new state surface
- a new message surface
- public search-policy ontology

Instead, it uses the new `SearchAssessment` layer to make several previously partial
search families credibly modelable with the same barriered runtime.

## What widened

With V2, the runtime now credibly supports three assessment-bearing families:

- `frontier_verify`
- `judge_reduce`
- `branch_execute_verify`

These are not separate runtime modes. They are recipe-level expressions over the
same public assessment-bearing surface:

- `SearchAssessment`
- `SearchRun.assessments`
- `SearchEvent.assessment_ids`
- barriered assessment gates

## Why this matters for E4

Before V2, these families could be sketched using:

- event metadata
- message payloads
- score vectors

That was enough to demonstrate shape pressure, but not enough to treat grounded
evaluation or adjudication as stable search truth.

V2 fixes that narrow gap:

- verifier outputs become explicit assessment records
- judge outputs become explicit assessment records
- branch execute/verify outcomes become explicit assessment records
- selection, pruning, and termination can now be attributed to inspectable
  assessment-backed gates

This is what widens coverage. The widening does **not** come from bespoke one-off
logic per recipe.

## Credible family packet

The widening packet is deliberately small:

1. `frontier_verify`
   - frontier candidates are assessed before selection
   - grounded verifier truth is visible in the run and events

2. `judge_reduce`
   - pairwise candidate comparison is represented as explicit assessment truth
   - pruning and selection remain barriered and inspectable

3. `branch_execute_verify`
   - branch-local search outcomes can be executed and assessed without shared
     mutable world state
   - merge/discard semantics remain separate from assessment truth

## What V2 still does not claim

This widening packet does **not** claim:

- async frontier search
- MCTS-style statistics
- hidden controller policies
- public training or RL control surfaces
- DARWIN-style orchestration

The V2 claim is narrower:

> one additional public primitive family, `SearchAssessment`, is sufficient to
> make multiple assessment-bearing search families credibly modelable without
> broadening the DAG kernel.

## Why the runtime stays maintainable

The assessment layer improves:

- maintainability: evaluator truth has one stable home
- atomicity: assessments are linkable without mutating message or state surfaces
- composability: recipes can reuse the same gate semantics
- interpretability: selection and pruning decisions point to concrete evidence
- forward compatibility: future verifier or judge backends can fit the same shape

That is the reason this packet is the stopping point for Phase 3 rather than the
start of a larger DAG-runtime expansion.
