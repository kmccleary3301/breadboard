# Coordination Intervention V1

## Purpose

This dossier defines the first host/human response loop that builds on the existing coordination substrate without adding a new kernel primitive.

The key design choice is deliberate:

- `bb.signal.v1` still records the need for intervention
- `bb.review_verdict.v1` still records supervisory interpretation
- `bb.directive.v1` now also carries the host response when a reviewed `human_required` path is answered

That keeps intervention on the existing truth stack instead of inventing a second coordination ontology.

---

## Scope

The first live slice covers one narrow pattern:

1. runtime or worker emits accepted `human_required`
2. supervisor records reviewed `human_required`
3. supervisor may emit an `escalate` directive to mark that human attention is outstanding
4. host answers with a durable directive such as:
   - `continue`
   - `checkpoint`
   - `terminate`
5. read-only inspection surfaces show the pending or resolved intervention state

This is enough to prove a real request/response loop.

---

## Why there is no new kernel primitive

The response loop does not require a new `bb.intervention.v1` kernel record yet.

The durable truth we already need is:

- the original signal
- the reviewed verdict
- the host-issued directive

Anything richer at this stage would mainly add ontology, not leverage.

The intervention snapshot is therefore projection:

- derived from existing durable truth
- read-only
- useful for hosts, TS parity, and operator inspection
- not itself canonical kernel truth

---

## Current live semantics

### Pending intervention

An intervention is pending when:

- there is a reviewed `human_required` verdict
- and there is no host-issued directive based on that verdict yet

### Resolved intervention

An intervention is resolved when:

- there is a reviewed `human_required` verdict
- and at least one host-issued directive is durably recorded against that verdict

### Host response vocabulary

The intentionally narrow first slice allows:

- `continue`
- `checkpoint`
- `terminate`

`continue` and `checkpoint` may wake the target task.

`terminate` records durable intent without requiring a wake target.

### Policy hardening

The current hardening layer stays narrow and inspectable:

- `coordination.review.allowed_reviewer_roles` constrains which reviewer roles may issue durable review truth
- host intervention now requires a prior supervisor `escalate` directive by default
- when a reviewed `human_required` path carries `support_claim_ref`, host actions may be narrowed by `coordination.intervention.support_claim_limited_actions`

That is enough to keep host override explicit without introducing a broader authority DSL.

---

## Inspection boundary

Read-only inspection may expose:

- accepted coordination signals
- review verdicts
- directives
- unresolved interventions
- resolved interventions
- host response lineage
- effective `allowed_host_actions` after support-aware policy is applied

It must not become a mutation API or a second semantic layer.

The current inspection snapshot should now be treated as the stable cross-lane read-only projection shape.

Adjacent lanes such as longrun summaries, Backbone inspection, and Host Kit projection helpers should consume that shared shape rather than deriving ad hoc coordination views of their own.

---

## Non-goals

This dossier does not define:

- a host write API
- approval packages
- authority domains
- channels
- rendezvous
- a new kernel intervention schema

---

## Evidence

The first tracked conformance slice is:

- `coordination/intervention_continue_fixture.json`

It proves:

- reviewed `human_required`
- durable supervisor escalation
- durable host-issued `continue`
- read-only pending/resolved intervention snapshots
- replay-safe directive idempotence
