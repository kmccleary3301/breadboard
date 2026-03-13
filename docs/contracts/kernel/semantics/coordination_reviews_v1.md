# Coordination Reviews V1

## Purpose

This dossier defines the next kernel truth layer above `bb.signal.v1`: explicit reviewed-result coordination.

V2 proved that workers can emit typed signal truth and supervisors can wake durably from typed subscriptions. The remaining gap is that supervisor-side outcomes were still too easy to trap inside orchestrator-private decision strings.

`bb.review_verdict.v1` is the first corrective slice.

---

## Contract role

`bb.review_verdict.v1` records a reviewer-owned outcome about prior coordination truth.

It sits:

- above worker-emitted or runtime-emitted signal truth
- below future directive truth
- below host/UI projection
- below backend transport details

It is durable reviewed truth, not a model-visible summary and not a hidden runtime convention.

---

## Core semantics

### 1. Signals remain the fact substrate

Signals still say what happened or what was proposed:

- a worker signaled `complete`
- a worker signaled `blocked`
- longrun/runtime signaled `no_progress`
- longrun/runtime signaled `retryable_failure`
- a worker signaled `human_required`

Review verdicts do not replace that fact layer.

### 2. Verdicts record reviewer-owned outcomes

Review verdicts answer questions like:

- was the completion claim validated
- is the worker result still pending validation
- should a blocked path be retried
- should a blocked path checkpoint
- should the path escalate
- is human review required

That distinction is what keeps mission ownership explicit.

### 3. Contracts and verdicts are different

`coordination.done`, `coordination.review`, and `coordination.merge` are public contract/policy surfaces.

`bb.review_verdict.v1` is the durable record of what actually happened when those policies were applied.

The current small review-policy expansion is:

- `coordination.review.no_progress_action`
- `coordination.review.retryable_failure_action`

Those are narrow mappings, not a general workflow DSL.

### 4. Review and directive stay separate

Review verdicts answer what the reviewer concluded.

Directives answer what durable action intent follows from that conclusion.

That separation is what keeps verdict truth from turning into a hidden control-plane DSL.

---

## First-tranche verdict vocabulary

The narrow R1 verdict vocabulary is:

- `validated`
- `pending_validation`
- `retry`
- `checkpoint`
- `escalate`
- `human_required`
- `noted`

This is intentionally smaller than a future approval or control-plane ontology.

The currently live actionable mappings are now:

- `blocked` -> `retry` / `checkpoint` / `escalate`
- `no_progress` -> currently narrowed by policy to `checkpoint` by default
- `retryable_failure` -> currently narrowed by policy to `retry` by default
- `human_required` -> reviewed `human_required`

---

## Reference slice meaning

In the current sparse supervisor-worker lane:

1. worker emits accepted `complete` or `blocked`
2. supervisor wakes from typed subscription
3. supervisor emits `bb.review_verdict.v1`
4. mission completion becomes durable only when the verdict validates it

That keeps three separate layers inspectable:

- producer fact
- reviewer verdict
- downstream mission completion or future action

---

## Non-goals

This dossier does not yet define:

- `bb.directive.v1`
- approval chains
- channels
- authority domains
- rendezvous or merge primitives
- a broad public coordination DSL

---

## Immediate next steps

1. keep review/verdict truth narrower than directive truth
2. keep directive truth narrower than the future multi-worker coordination ontology
3. prove a reducer-style multi-worker aggregation slice before entertaining richer coordination ontology

## Reducer-style aggregation note

The next valid widening after sparse supervisor-worker is not channels or rendezvous.

It is:

- one supervisor
- two shard workers
- one reducer/aggregator

In that slice:

- shard signals remain ordinary signal truth
- reducer output becomes the reviewed subject the supervisor validates
- aggregate result shape is governed by `coordination.merge.reducer_result_contract`

That keeps fan-in explicit without overproducing ontology.

## Read-only inspection note

The current justified inspection surface is intentionally read-only:

- session/runtime snapshots may expose accepted signals, review verdicts, directives, and latest-by-code summaries
- those snapshots are projections over durable truth, not a second semantic layer
- hosts and TS surfaces should inspect them, not mutate them
