# Optimization V1 Reflective Backend

Phase D of BreadBoard Optimization V1 adds the first backend.

This backend is intentionally small, typed, and bounded. It is not a campaign runtime, not an island model, and not a generic prompt mutator.

## Phase D records

The Phase D records live in:

- `agentic_coder_prototype/optimize/backend.py`
- `agentic_coder_prototype/optimize/examples.py`

The main records are:

- `ReflectiveParetoBackendRequest`
- `ReflectiveParetoBackendResult`
- `ObjectiveVector`
- `CandidatePortfolio`
- `PortfolioEntry`
- `ReflectionDecision`
- `MutationBounds`
- `MutationProposal`

## Backend philosophy

Optimization V1 starts with a reflective Pareto backend because BreadBoard needs a search loop that can:

1. consume structured wrongness rather than vague score deltas
2. compare candidates on multiple dimensions
3. preserve useful tradeoff candidates
4. mutate typed loci only
5. prefer narrow overlays over broad rewrites

The current objective vector is explicit:

- `correctness_score`
- `wrongness_penalty`
- `mutation_cost`
- `instability_penalty`

The comparison protocol is also explicit:

- `correctness_score` is maximized
- `wrongness_penalty` is minimized
- `mutation_cost` is minimized
- `instability_penalty` is minimized

That directionality is part of the public contract so Pareto dominance is not ambiguous.

## Typed mutation policies versus generic prompt mutation

The backend does not edit arbitrary text blobs.

Instead, the mutation policy receives:

- an `OptimizationTarget`
- a baseline candidate and materialized candidate
- a `ReflectionDecision`
- `MutationBounds`

It may only produce candidates that:

- touch declared `MutableLocus` entries
- stay within configured blast-radius limits
- remain overlay-shaped rather than whole-artifact rewrites
- preserve the current support envelope

This matters because BreadBoard needs optimization artifacts that are auditable and safe to gate later.

## Reflection policy inputs

The current reflection policy is wrongness-guided.

It does not mutate because an aggregate score was low. It mutates because structured evidence says:

- what wrongness class occurred
- where the failure happened
- where repair is likely to happen
- whether the confidence is high enough to justify bounded mutation

If the wrongness evidence is missing, low-confidence, or points outside the target, the policy declines mutation.

## Non-dominated retention

The backend keeps a candidate portfolio rather than a single winner.

That portfolio uses Pareto retention so multiple candidates can survive when they represent real tradeoffs, for example:

- one narrower overlay with lower mutation cost
- one slightly broader overlay with better predicted correctness

Duplicate overlays are deduplicated by candidate fingerprint, and equal-score ties resolve stably by candidate ID.

## Blast-radius rules

Phase D makes blast radius explicit through `MutationBounds`.

The current bounded checks include:

- maximum changed loci
- maximum changed artifacts
- maximum serialized overlay payload size

Candidates also fail validation if they:

- reference unknown loci
- use non-mapping whole-value rewrites instead of overlay-shaped changes
- violate built-in invariants
- widen the support envelope after materialization

## Canonical example

The canonical Phase D example is the codex dossier backend example in `agentic_coder_prototype/optimize/examples.py`.

It uses the existing substrate, dataset, and evaluation example path to run one reflective backend pass. The example produces:

- one backend request
- one reflection decision
- two bounded follow-on candidates
- a retained non-dominated portfolio

Minimal usage:

```python
from agentic_coder_prototype.optimize import build_codex_dossier_backend_example

example = build_codex_dossier_backend_example()
request = example["request"]
result = example["result"]

assert request.request_id == result.request_id
assert result.reflection_decision.should_mutate is True
assert len(result.proposals) == 2
```

If you want serialized payloads:

```python
from agentic_coder_prototype.optimize import build_codex_dossier_backend_example_payload

payload = build_codex_dossier_backend_example_payload()
```

## Anti-overlap rule with DARWIN

This backend is deliberately not DARWIN.

Phase D does not introduce:

- islands
- archives
- migration or topology state
- open-ended asynchronous campaign semantics

The backend API is generic enough that a future implementation could plug into other search strategies, but V1 remains coherent and useful without any DARWIN component.
