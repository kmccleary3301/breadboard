# Coordination Signals V1

## Purpose

This dossier defines the first typed coordination-truth family for BreadBoard multi-agent orchestration.

The problem being solved is not "missing a bus." The problem is that current wakeup and completion behavior is still too projection-shaped:

- model-visible wakeup notices can carry semantic weight
- completion ingress paths can act too much like truth
- `wake_conditions` mixes signal-like and non-signal-like behavior without a typed coordination layer

`bb.signal.v1` and `bb.wake_subscription.v1` are the first corrective slice.

---

## Contract role

These contracts sit:

- above backend transport like Temporal signal names or workflow updates
- above model-visible wakeup notices
- above text sentinels and tool/provider finish heuristics
- below host UX and dossier/product surfaces

They represent durable coordination truth, not projection conveniences.

---

## Primitive family

### `bb.signal.v1`

Typed coordination record representing a proposed or validated coordination fact.

The first frozen codes are:

- `partial_complete`
- `merge_ready`
- `complete`
- `blocked`
- `no_progress`
- `retryable_failure`
- `catastrophic_failure`
- `human_required`

Not every code needs to be exercised on day one, but the vocabulary should be explicit.

### `bb.wake_subscription.v1`

Typed wake rule embedded on a distributed task descriptor.

The first safe V1 behavior is intentionally narrow:

- subscribe by signal code
- optionally scope by source task ids
- resume when matched
- support durable cursor + dedupe later in orchestration/runtime layers

---

## Core semantics

### 1. Ingress is not truth

These still exist:

- text sentinels
- `mark_task_complete`
- provider finish reasons

But they become:

> ingress -> signal proposal -> validation -> accepted/rejected signal truth

### 2. Visibility is not authority

A signal may become model-visible or host-visible later through projection. That does not grant the emitter completion authority.

### 3. Worker completion is proposal-level

Workers may emit `complete`. They do not get to declare mission completion as durable mission truth by themselves.

### 4. Wake subscriptions augment legacy wakes

`wake_subscriptions` does not replace `wake_conditions` in the first tranche.

The split is:

- `wake_subscriptions`: typed signal-based wakes
- `wake_conditions`: legacy strings and non-signal wakes, especially timer-style behavior

### 5. Transport is downstream

Temporal, longrun controllers, and host/UI projections can compile or display these signals, but they do not define the semantics.

### 6. Public config stays narrow

The first public dossier/config surface should stay small:

- `coordination.mission_owner_role`
- `coordination.legacy_completion_sources`
- `coordination.preserve_legacy_wake_conditions`

That is enough to make ownership and additive migration explicit without introducing directives, channels, or broader authority frameworks too early.

---

## Compatibility mapping

### `completion.*` is ingress

The current completion surface remains real, but it is compatibility ingress:

- `completion.text_sentinels` -> may yield `text_sentinel` signal proposals
- `completion.tool_finish` / `mark_task_complete` -> may yield `tool_call` signal proposals
- `completion.provider_signals` -> may yield `provider_finish` signal proposals

Those settings do not become durable coordination truth by themselves.

### `multi_agent.bus.model_visible_topics` is projection

`multi_agent.bus.model_visible_topics` controls projection, not semantics.

In practice:

- accepted signals may produce wake-derived projection events
- the `wakeup` topic may inject model-visible messages
- that visibility remains downstream of accepted signal truth

The bus is therefore a host/model-visible surface, not the source of coordination authority.

### `wake_conditions` remains additive

`wake_conditions` is still preserved for:

- timers
- legacy string wakes
- non-signal wake semantics

`wake_subscriptions` is where typed signal-based wake truth now lives.

---

## First-tranche freeze

The first live coded slice should prove:

- typed `complete` and `blocked` signals
- additive `wake_subscriptions`
- accepted vs rejected validation results
- sparse supervisor wake behavior downstream of accepted signals

The following remain explicitly deferred:

- `bb.directive.v1`
- channel schemas
- authority-domain schemas
- rendezvous / merge objects
- large coordination APIs

---

## Why this matters

Without a typed coordination family, BreadBoard risks keeping completion and wake behavior trapped inside:

- prompt-visible messages
- tool-specific heuristics
- backend-specific orchestration paths

That would make deeper hierarchy work brittle and hard to replay honestly.

---

## Immediate next steps

1. keep the public `coordination:` surface narrow and explicit
2. map compatibility from `completion`, `multi_agent.bus`, and `wake_conditions` without blurring ownership
3. promote the sparse supervisor-worker evidence into manifest-backed conformance rows
