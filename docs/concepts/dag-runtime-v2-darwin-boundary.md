# DAG Runtime V2 DARWIN Boundary

DAG Runtime V2 remains a bounded per-task search substrate.

DARWIN remains the outer-loop research and campaign system.

## DAG runtime owns

- candidate/frontier/event truth
- assessment-bearing search
- barriered selection, pruning, and termination
- branch-local state for one bounded search episode
- trajectory export for downstream analysis

## DARWIN owns

- cross-task budget allocation
- persistent campaign state
- archive, island, or diversity pressure
- long-horizon research orchestration
- many-run strategy comparisons across episodes

## Why the boundary matters

If DAG absorbs DARWIN semantics too early, the runtime loses:

- maintainability
- atomicity
- interpretability
- intuitive configuration

V2 therefore adds no campaign nouns and no archive nouns.

The handoff should only happen if repeated live use reveals pressure like:

- many-cohort async orchestration
- persistent diversity/archive bookkeeping
- cross-task scheduling policy that is not reducible to one bounded search run
