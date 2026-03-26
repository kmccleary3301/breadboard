# Optimization V1 Evaluation, Diagnostics, And Wrongness

Phase C of BreadBoard Optimization V1 adds the evaluation layer.

This is the layer that turns typed targets and truth-backed samples into evidence-bearing evaluation records.

## Phase C records

The Phase C records live in:

- `agentic_coder_prototype/optimize/evaluation.py`
- `agentic_coder_prototype/optimize/diagnostics.py`
- `agentic_coder_prototype/optimize/wrongness.py`
- `agentic_coder_prototype/optimize/examples.py`

The main records are:

- `EvaluationRecord`
- `DiagnosticBundle`
- `DiagnosticEntry`
- `WrongnessReport`

## Why lineage must be explicit

BreadBoard should never force an engineer to reverse-engineer what was evaluated.

That means an evaluation record must carry explicit lineage:

- target ID
- candidate ID
- dataset ID
- dataset version
- sample ID
- evaluator ID
- evaluator version

Those are not optional convenience fields. They are the minimum contract needed to make optimization interpretable, replayable, and auditable.

## Diagnostics versus wrongness

These are related, but they are not the same thing.

### Diagnostics

Diagnostics capture what the evaluator observed:

- replay-gate results
- support-envelope diffs
- cache identity
- determinism class
- evaluator mode
- reproducibility assumptions

Diagnostics are the normalized machine-readable observation layer.

### Wrongness

Wrongness explains why the candidate is wrong:

- what class of failure occurred
- where it failed
- how confident we are
- what evidence supports the judgment
- where repair should probably happen

Wrongness is the judgment layer built on top of diagnostics and evidence.

## Evaluator mode taxonomy

BreadBoard Optimization V1 currently treats evaluator mode as one of:

- `replay`
- `hybrid`
- `live`

This matters because acceptance rules, caching rules, and evidence burden change depending on the mode.

## Determinism taxonomy

Diagnostic bundles currently treat determinism class as one of:

- `deterministic`
- `seeded_stochastic`
- `unseeded_stochastic`
- `environment_volatile`

This matters because cache behavior, retry behavior, and confidence in promotion should all depend on how stable the evaluator actually is.

## Wrongness taxonomy

Phase C uses a strict wrongness taxonomy rather than arbitrary labels.

Current classes include:

- correctness failures
- support-envelope and invariant violations
- replay/conformance drift
- environment/tool mismatches

This is deliberate. Scalar score only is not enough for BreadBoard.

A higher score cannot explain:

- why the candidate is unsafe
- why replay drift occurred
- why a support claim widened
- where the repair should happen

## Canonical example

The canonical Phase C example is the codex dossier evaluation example in `agentic_coder_prototype/optimize/examples.py`.

It layers on top of the existing codex dossier substrate and dataset examples and includes:

- one `EvaluationRecord`
- one `DiagnosticBundle`
- one `WrongnessReport`

The example intentionally shows a replay-stable candidate that still gets rejected because the support envelope drifted. That is a useful example because it proves BreadBoard optimization is not just “replay stayed green, so promote it.”

Minimal usage:

```python
from agentic_coder_prototype.optimize import build_codex_dossier_evaluation_example

example = build_codex_dossier_evaluation_example()
evaluation = example["evaluation"]
diagnostics = example["diagnostics"]
wrongness = example["wrongness_report"]

assert evaluation.evaluation_id == diagnostics.evaluation_id
assert wrongness.failure_locus == "tool.render.exec_command"
```

If you want serialized payloads:

```python
from agentic_coder_prototype.optimize import build_codex_dossier_evaluation_example_payload

payload = build_codex_dossier_evaluation_example_payload()
```

## What Phase C completes

Phase C completes the evaluation substrate by adding:

- explicit evaluation records
- explicit diagnostics
- explicit wrongness
- mode and determinism contracts
- cache/reproducibility capture

It still stops before:

- backend search logic
- promotion state machines
- final replay/conformance gate orchestration

Those belong to later phases.
