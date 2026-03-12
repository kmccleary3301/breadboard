# Optimization V1 Datasets And Ground Truth

Phase B of BreadBoard Optimization V1 adds the truth layer that sits on top of the substrate primitives.

The goal is simple: optimization should not be driven by vague taste or scalar scores alone. It needs explicit samples, explicit truth, and explicit reasons why the truth is correct.

## Phase B records

The Phase B records live in:

- `agentic_coder_prototype/optimize/dataset.py`
- `agentic_coder_prototype/optimize/examples.py`

The main records are:

- `OptimizationDataset`
- `OptimizationSample`
- `GroundTruthPackage`
- `CorrectnessRationale`

## Why this layer exists

Without a truth layer, an optimizer will drift toward whatever is easiest to score.

That is exactly how you end up with:

- prompt slop
- benchmark theater
- golden-trace overfitting
- support-envelope widening
- “better” outputs that are actually less correct

BreadBoard Optimization V1 treats that as unacceptable.

The truth layer exists so that each important optimization sample can answer:

1. what result is expected
2. what evidence supports that expectation
3. what behavior is allowed
4. what behavior is forbidden
5. what checks should run before we trust the sample outcome

## Record roles

### `CorrectnessRationale`

This is the explanation of why the expected result is correct.

It is not just a label or a score note. It should contain:

- a concise summary
- a full explanation
- evidence refs
- acceptance clauses
- forbidden-behavior clauses

If the sample matters, the rationale is not optional.

### `GroundTruthPackage`

This is the truth payload attached to a sample.

It may include:

- expected structured result fields
- expected artifacts
- executable checks
- admissible alternatives
- rationale refs

This separation is intentional. The package holds the truth contract; the rationale explains why that contract is legitimate.

### `OptimizationSample`

This is one optimization case.

It binds together:

- the target being optimized
- the prompt/task input
- environment requirements
- bound-tool requirements
- expected checks
- the ground-truth package

This is where environment and tool-pack assumptions start to become explicit in a reusable way.

### `OptimizationDataset`

This is the immutable dataset container.

It carries:

- dataset ID and version
- samples
- split definitions
- scope notes
- reproducibility metadata
- rationale catalog

The dataset validates that:

- sample IDs are unique
- rationale IDs are unique
- every sample references known rationales
- split definitions do not reference unknown samples

## Anti-slop guidance

This part is non-optional for BreadBoard.

Optimization datasets should avoid:

- underspecified “good answer” judgments
- one-line rationales with no evidence
- broad style-only rewards with no correctness anchor
- hidden support widening
- overfitting to a single exact wording when multiple behaviors are valid

Instead, prefer:

- explicit acceptance clauses
- explicit forbidden-behavior clauses
- executable checks where possible
- admissible alternatives when multiple valid outcomes exist
- reproducibility metadata that explains how the sample should be replayed or evaluated

## Canonical example

The canonical Phase B example is the codex dossier dataset example in `agentic_coder_prototype/optimize/examples.py`.

It models a narrow sample that optimizes two declared overlay loci while requiring:

- preserved support envelope
- replay-safe behavior
- declared-loci-only changes

Minimal usage:

```python
from agentic_coder_prototype.optimize import build_codex_dossier_dataset_example

example = build_codex_dossier_dataset_example()
dataset = example["dataset"]
sample = example["sample"]
truth = example["ground_truth_package"]
rationale = example["correctness_rationale"]

assert dataset.sample_ids() == [sample.sample_id]
assert truth.rationale_refs == [rationale.rationale_id]
```

If you want serialized fixture-like payloads:

```python
from agentic_coder_prototype.optimize import build_codex_dossier_dataset_example_payload

payload = build_codex_dossier_dataset_example_payload()
```

## What Phase B still does not do

Phase B stops before:

- wrongness reports
- evaluation records
- evaluator capture plans
- backend search logic
- promotion state machines

Those belong to later phases. Phase B only establishes the dataset and truth contract the rest of the optimization stack will rely on.
