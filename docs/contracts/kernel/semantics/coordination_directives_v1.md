# Coordination Directives V1

## Purpose

This dossier defines the narrow directive truth layer that sits above explicit review/verdict truth.

Signals say what happened. Review verdicts say how a reviewer interpreted that signal. Directives say what durable supervisory action intent follows from that review.

`bb.directive.v1` is intentionally narrow.

---

## Contract role

`bb.directive.v1` records reviewer-owned action intent that is durable, replayable, and inspectable.

It sits:

- above signal truth
- above review/verdict truth
- below backend transport and wakeup projection
- below any future richer public control surface

---

## First-tranche directive vocabulary

The narrow V1 directive vocabulary is:

- `continue`
- `retry`
- `checkpoint`
- `escalate`
- `terminate`

This is not a general workflow DSL.

---

## Core semantics

### 1. Directives are not verdicts

Review verdicts answer what the reviewer concluded.

Directives answer what durable action intent follows from that conclusion.

### 2. Directives are not transport

Temporal update names, runtime wake calls, and model-visible notices are downstream realizations of directive truth. They do not define the directive semantics.

### 3. Directives remain target-local in the first slice

The R2 slice only proves narrow task-directed intent:

- continue this worker
- retry this worker
- checkpoint this worker

Escalation and termination are schema-valid but not broadly exercised yet.

---

## First live slice

The first coded slice should prove:

- `pending_validation` review verdict -> `continue` directive
- `blocked` + retry review verdict -> `retry` directive
- `blocked` + checkpoint review verdict -> `checkpoint` directive

That is enough to make action intent explicit without widening the ontology.

The currently widened live slice now also proves:

- reviewed `no_progress` -> `checkpoint`
- reviewed `retryable_failure` -> `retry`
- reviewed `human_required` -> `escalate`
- reviewed `human_required` + host response -> host-issued `continue` / `checkpoint` / `terminate`

Those are still deliberately narrow and policy-shaped.

---

## Non-goals

This dossier does not yet define:

- channel routing
- authority domains
- batching
- priorities
- approval chains
- host-facing workflow packages

---

## Immediate next steps

1. keep directives narrower than the eventual coordination ontology
2. prove directives replay cleanly through the same evidence lane as signals and review verdicts
3. use directives in the reducer-style multi-worker slice before considering richer coordination patterns

## Reducer slice implication

In the reducer-style multi-worker slice, directives should still stay narrow:

- continue the reducer when aggregation is incomplete
- retry or checkpoint the reducer when a blocked shard forces a supervisory decision

That is enough to prove fan-in action intent without inventing routing or channel DSLs.

## Inspection boundary

Read-only inspection may expose directive truth and directive lineage, but it should not become a mutation surface.

Directive creation remains below projection and above review truth.

## Host intervention note

The first host/human response loop does not add a new kernel intervention primitive.

Instead:

- the supervisor may issue `escalate` from reviewed `human_required`
- the host may later issue `continue`, `checkpoint`, or `terminate`
- pending/resolved intervention state is derived from directive lineage

That keeps host action on the same durable directive truth surface as supervisory action.

## Delegated verification note

In the delegated-verification slice, directive truth still stays narrow:

- verifier `blocked` + reviewed checkpoint -> `checkpoint` directive to the verifier task

The verifier pattern therefore extends the existing action language rather than requiring a new verification-control primitive.
