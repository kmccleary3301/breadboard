# Coordination Verification V1

## Purpose

This dossier defines the first delegated-verification pattern built on the existing coordination substrate.

The pattern is intentionally small:

1. a worker produces a result
2. a verifier task produces verification evidence about that result
3. the supervisor reviews the verifier outcome, not the worker output directly

This proves a higher-order coordination pattern without adding a new kernel primitive.

---

## Contract center

The kernel truth chain remains unchanged:

- `bb.signal.v1`
- `bb.review_verdict.v1`
- `bb.directive.v1`

What this slice adds is a small result bundle contract:

- `bb.coordination_verification_result.v1`

That contract is carried inside signal payloads. It is not a new kernel event family.

---

## Why a result contract is justified here

Without a shared verification bundle shape, the pattern quickly turns into payload sludge:

- which worker result was verified
- which verifier produced the evidence
- whether verification passed or failed
- which artifact refs justify the verification result

That structure is repeated enough to deserve a contract, but not enough to deserve a new primitive.

---

## First live slice

The first proof lane is:

- worker emits accepted `complete`
- verifier wakes from typed subscription
- verifier emits either:
  - `complete` with verification result status `pass`, or
  - `blocked` with verification result status `fail` or `soft_fail`
- supervisor wakes from the verifier signal
- supervisor validates or checkpoints based on the verifier signal

This keeps supervisory approval explicit while making verification evidence first-class enough to inspect.

---

## Current bundle shape

`bb.coordination_verification_result.v1` currently carries:

- `subject_signal_id`
- `subject_task_id`
- `validator_task_id`
- `status`
- `verification_artifact_refs`
- `summary`

The status vocabulary is intentionally small:

- `pass`
- `fail`
- `soft_fail`

---

## Current review behavior

When `coordination.review.verification_result_contract` is configured:

- verifier `complete` remains pending unless the verification bundle is present and valid
- verifier `complete` requires verification status `pass`
- verifier `blocked` requires verification status `fail` or `soft_fail`
- the referenced subject signal must exist and be an accepted `complete`

That keeps delegated verification honest without widening the ontology.

When verification or intervention paths carry `support_claim_ref`, later host override remains policy-shaped rather than implicit:

- the reviewed truth still records the support claim reference directly
- host action may be narrowed by intervention policy
- verification evidence stays evidence, not hidden authority

---

## Non-goals

This dossier does not define:

- adjudication across multiple candidate branches
- comparison scoring
- branch selection policy
- a new kernel verification primitive

---

## Evidence

The first tracked delegated-verification fixtures are:

- `coordination/delegated_verification_pass_fixture.json`
- `coordination/delegated_verification_fail_fixture.json`

They prove:

- worker-result lineage into verifier evidence
- supervisor review over verifier output
- verification-result contract validation
- checkpoint issuance when verification fails
