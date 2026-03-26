# DAG Runtime V1 Search Surface

DAG Runtime V1 introduces a new opt-in `search` subsystem for BreadBoard.

This first tranche is intentionally small.

It does **not** add:

- `rsa_mode`
- `pacore_mode`
- async frontier scheduling
- a public training or RL-control surface
- DARWIN-style campaign semantics

It adds:

- immutable search records:
  - `SearchCandidate`
  - `SearchMessage`
  - `SearchCarryState`
  - `SearchFrontier`
  - `SearchEvent`
  - `SearchRun`
- a deterministic barriered scheduler
- one RSA-style recipe over subset aggregation
- one typed compaction registry with bounded carry-over
- one bounded multi-round message-passing recipe with final synthesis
- one branch-local state slice with explicit snapshots and merge/discard semantics
- one trajectory export layer with bounded local/global reward hooks
- explicit search metrics:
  - `aggregability_gap`
  - `diversity_decay`
  - `mixing_rate`

## Why this shape

BreadBoard already has adjacent surfaces:

- `RLM` for recursive call structure
- `LongRun` for bounded durable time
- `C-Trees` for durable memory and compaction

The missing surface is explicit external search state.

That is what this V1 cut establishes.

## What the first tranche proves

The first DAG cut proves:

1. search truth can live outside hidden controller state
2. the scheduler can remain barriered and deterministic
3. RSA is expressible as a recipe over the shared runtime
4. multi-parent lineage is a first-class concept without redefining C-Trees
5. carried search state can stay bounded and inspectable through typed compaction
6. a PaCoRe-like loop can reuse the same runtime without hidden context growth
7. stateful branches can remain local and inspectable without collapsing into one mutable workspace
8. offline research exports can be produced without changing the search truth model

## What remains intentionally deferred

- broader verifier-driven stateful search programs
- online training or policy-learning systems
- DARWIN-side orchestration or campaign semantics

The point of this cut is to validate the architectural center before widening the system.

## Current V1 status

The current DAG worktree covers:

- `Phase 0` search truth surface
- `Phase 1` barriered RSA recipe
- `Phase 2` typed compaction and carry-over
- `Phase 3` one bounded message-passing recipe
- `Phase 4` branch-local state and merge/discard semantics
- `Phase 5` trajectory export with bounded reward hooks

What remains deferred inside V1:

- no additional scoped V1 phases remain
- broader RL/training programs remain intentionally outside this V1 cut
