# Optimization V1 Promotion And Safety

Phase E of BreadBoard Optimization V1 adds the promotion layer.

This is the layer that decides whether a candidate can move from "useful on the frontier" to "safe enough to bless". Improvement alone is not enough.

## Phase E records

The Phase E records live in:

- `agentic_coder_prototype/optimize/promotion.py`
- `agentic_coder_prototype/optimize/examples.py`

The main records are:

- `PromotionRecord`
- `PromotionDecision`
- `PromotionEvidence`
- `GateResult`
- `ReplayConformanceGateInput`
- `SupportEnvelopeGateInput`

## State machine

Promotion is an explicit state machine.

The current states are:

- `draft`
- `evaluated`
- `frontier`
- `gated`
- `promotable`
- `promoted`
- `rejected`
- `archived`

These states are intentionally separate.

### What the states mean

- `draft`: the candidate entered the workflow but no evaluation evidence has been attached yet.
- `evaluated`: the candidate has evaluation lineage, but frontier and gate decisions have not been made yet.
- `frontier`: the candidate remains interesting enough to retain, but it has not passed all promotion gates.
- `gated`: the candidate has had promotion gates executed and the gate outputs are now part of the lineage.
- `promotable`: the candidate has passed the required promotion gates and is eligible for blessing.
- `promoted`: the candidate was actually blessed into the tracked surface.
- `rejected`: the candidate failed a required promotion gate and should not advance.
- `archived`: the record is preserved for lineage, but it is no longer active.

## Why `frontier` is not `promotable`

This distinction matters.

A frontier candidate may still be worth keeping because it is:

- non-dominated in the portfolio
- narrower than an alternative
- promising but under-evaluated
- blocked only by missing evidence

That does not mean it is safe to promote.

BreadBoard treats `frontier` as "keep it under consideration" and `promotable` as "it has cleared the safety and evidence gates needed for blessing."

## Replay-aware gating

Replay-aware gating is explicit, not inferred from a generic outcome label.

The replay gate looks at:

- target support-envelope assumptions such as `requires_replay_gate`
- evaluation input compatibility metadata such as `replay`
- evaluator mode from diagnostic bundles
- replay-specific evidence lineage
- recorded replay gate outcomes

The gate can produce:

- `pass`
- `fail`
- `not_applicable`
- `insufficient_evidence`

That last state is important. Missing replay evidence is not the same thing as a replay failure.

## Conformance-aware gating

Conformance gating is also explicit.

The conformance gate looks at:

- declared schema compatibility
- declared conformance bundle metadata
- recorded conformance gate outcomes
- conformance evidence lineage when present

This keeps promotion tied to the same evaluation-contract surfaces that the rest of BreadBoard already uses, rather than turning promotion into a freeform judgment layer.

## Support-envelope gating

Support-envelope gating is a first-class promotion gate.

It compares the target envelope against the materialized candidate envelope across:

- tools
- execution profiles
- environments
- providers
- models
- assumptions

The current policy allows exact preservation and acceptable narrowing, but it rejects widening even if the candidate otherwise looks better.

That rule is the point. BreadBoard does not allow silent support widening to piggyback on optimization gains.

## Improvement without safety evidence is not promotable

This is the core policy of Phase E.

A candidate can improve:

- correctness
- wrongness profile
- mutation cost tradeoff
- portfolio retention value

and still fail promotion if it lacks:

- replay evidence where replay is required
- conformance evidence where conformance is required
- support-envelope preservation
- explicit evidence lineage

Optimization V1 is evidence-gated by design.

## Canonical examples

The canonical Phase E examples live in `agentic_coder_prototype/optimize/examples.py`.

They reuse the existing codex dossier substrate, dataset, evaluation, and backend path to produce three promotion outcomes:

- a `frontier` candidate blocked by missing replay/conformance evidence
- a `rejected` candidate that widens the support envelope
- a `promotable` candidate that passes replay, conformance, and support-envelope checks

Minimal usage:

```python
from agentic_coder_prototype.optimize import build_codex_dossier_promotion_examples

example = build_codex_dossier_promotion_examples()

assert example["promotable"]["record"].state == "promotable"
assert example["frontier_blocked"]["record"].state == "frontier"
assert example["support_fail"]["record"].state == "rejected"
```

If you want serialized payloads:

```python
from agentic_coder_prototype.optimize import build_codex_dossier_promotion_examples_payload

payload = build_codex_dossier_promotion_examples_payload()
```

## DARWIN anti-overlap boundary

Phase E still does not introduce DARWIN campaign state.

The promotion layer here is scoped to candidate blessing and safety evidence for Optimization V1. It does not add:

- island state
- archive migration logic
- topology semantics
- long-running campaign orchestration

That separation is intentional. Promotion remains BreadBoard-native and complete without any DARWIN component.
