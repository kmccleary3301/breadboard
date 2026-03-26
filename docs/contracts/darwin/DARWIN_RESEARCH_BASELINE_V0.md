# DARWIN Research Baseline V0

This baseline defines the bounded `lane.research` Phase-1 evaluator slice.

## Scope

- objective class: evidence synthesis
- evaluator type: reviewer-audit surrogate with deterministic citation scoring
- claim target: internal only
- budget class: `class_a`

## Baseline task family

The baseline pack is `fixtures/research_baseline_pack_v0.json`.

Each query defines:

- required references
- bridge references
- support references
- a bounded evidence pool

The lane runner emits a normalized score from:

1. weighted precision over selected evidence
2. weighted recall over required/bridge/support evidence
3. bridge-coverage bonus

## Supported strategies

- `precision_first` — baseline
- `coverage_first` — breadth mutation
- `bridge_synthesis` — bridge-preserving synthesis mutation

## Validity rules

A comparison counts only when:

1. the same research pack is used
2. the evaluator remains the deterministic citation scorer
3. the budget class is unchanged
4. topology compatibility is declared in `DARWIN_TOPOLOGY_COMPATIBILITY_V1.md`

This lane is operational for internal Darwin evidence only. It is not a publication-grade research benchmark.
