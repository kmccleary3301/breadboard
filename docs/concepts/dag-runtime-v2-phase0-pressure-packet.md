# DAG Runtime V2 Phase 0 Pressure Packet

DAG Runtime V2 starts with a go/no-go check, not an automatic kernel expansion.

The Phase 0 packet uses only `DAG Runtime V1` primitives and recipe-level metadata to test whether one missing public shape appears repeatedly.

## Study cells

The bounded cells are:

- verifier-guided code patch search
- judge/reducer reasoning search
- branch execute/verify search

Each cell is intentionally implemented without a new runtime primitive.

## What the packet found

Across all three cells, the same awkwardness appears:

- evaluator truth lives in `SearchEvent.metadata`
- supporting artifact refs are attached indirectly
- candidate selection or branch outcomes depend on grounded judgments that are not first-class runtime truth

The repeated missing shape is:

- `assessment_evaluator_truth`

## Why this matters

V1 already proved:

- explicit search state
- barriered scheduling
- typed carry-over
- bounded message passing
- branch-local state
- trajectory export

So the next justified pressure is not more search-state structure.

It is:

- a typed record for verifier/judge/adjudicator/execute-result truth

## What the packet does not justify

The Phase 0 packet does **not** justify:

- a new message primitive
- a new state primitive
- public async scheduling
- a public search-policy layer
- DARWIN-style campaign semantics

## Go/no-go outcome

The packet outcome is `go`, but only for a narrow V2:

- add one typed assessment truth carrier
- add explicit linkage from runs/events/trajectory export
- keep everything else frozen until further repeated-shape evidence appears
