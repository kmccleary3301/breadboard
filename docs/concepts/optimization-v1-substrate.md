# Optimization V1 Substrate

BreadBoard Optimization V1 starts with a small typed substrate rather than a generic prompt-mutation loop.

The point of the substrate is to make optimization **bounded, evidence-aware, and replay-safe** before we add datasets, wrongness reports, or backends.

## What exists in Phase A

The Phase A substrate lives in:

- `agentic_coder_prototype/optimize/substrate.py`
- `agentic_coder_prototype/optimize/examples.py`

The core records are:

- `OptimizationTarget`
- `MutableLocus`
- `CandidateBundle`
- `MaterializedCandidate`
- `SupportEnvelope`

Supporting records:

- `ArtifactRef`
- `OptimizationInvariant`
- `CandidateChange`

## Why bounded overlays matter

Optimization V1 is deliberately **not** based on rewriting giant unstructured blobs.

Instead, an optimization target declares a fixed set of mutable loci. A candidate is only valid if it edits those declared loci and nothing else.

That gives BreadBoard three important properties:

1. the blast radius is explicit
2. support-envelope and replay gates stay tractable
3. promotion can reason about what actually changed

In practical terms, this means a target should point at narrow surfaces like:

- one tool description
- one prompt section
- one support-claim heuristic
- one task-family overlay block

Not:

- an entire dossier rewrite
- a whole provider schema rewrite
- broad capability-surface churn

## Record roles

### `OptimizationTarget`

Defines the optimization surface:

- stable target ID
- target kind
- baseline artifact refs
- mutable loci
- support envelope
- invariants

### `MutableLocus`

Defines one typed region that may change:

- locus ID
- locus kind
- selector
- optional backing artifact ref
- mutation kind
- constraints

The selector is intentionally generic. It can be a dotted path, a section key, or another stable selector format as long as the target owner treats it consistently.

### `SupportEnvelope`

Makes the allowed execution/support context explicit:

- tools
- execution profiles
- environments
- providers
- models
- assumptions

This is the record that helps prevent accidental support widening during optimization.

### `CandidateBundle`

Represents a candidate overlay before promotion:

- candidate ID
- source target ID
- applied loci
- per-locus changes
- provenance

The bundle validates that:

- the target IDs match
- the candidate only references declared loci
- the candidate change set matches the declared applied loci

### `MaterializedCandidate`

Represents the resolved candidate after overlay application.

It carries:

- the effective artifact
- the effective tool surface
- the support envelope
- evaluation-input compatibility metadata

That lets later phases attach replay, conformance, and evaluation evidence without losing the linkage back to the bounded overlay.

## Example flow

The canonical Phase A example is the codex dossier example in `agentic_coder_prototype/optimize/examples.py`.

It models a target with two allowed loci:

- `tools.exec_command.description`
- `developer_prompt.optimization_guidance`

And it shows a candidate that changes only those two loci while preserving the target support envelope.

Minimal usage:

```python
from agentic_coder_prototype.optimize import build_codex_dossier_example

example = build_codex_dossier_example()
target = example["target"]
candidate = example["candidate"]
materialized = example["materialized"]

candidate.validate_against_target(target)
assert materialized.support_envelope == target.support_envelope
```

If you need a serialized shape for inspection or fixtures:

```python
from agentic_coder_prototype.optimize import build_codex_dossier_example_payload

payload = build_codex_dossier_example_payload()
```

## What Phase A does not do yet

Phase A stops before:

- datasets
- ground-truth packages
- correctness rationales
- wrongness reports
- evaluation records
- reflective Pareto search
- promotion state machines

Those belong to later phases. Phase A only establishes the typed substrate that those later layers can build on safely.
