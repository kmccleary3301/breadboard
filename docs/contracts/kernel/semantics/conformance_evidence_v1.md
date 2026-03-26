# Conformance Evidence V1

## Purpose

This dossier defines the shared contract family for conformance evidence.

BreadBoard already treats target-harness parity claims seriously by pinning versions, storing evidence, and recording strictness. The engine-contract program needs the same discipline for Python-vs-TS and contract-vs-engine claims.

---

## Contract role

A conformance-evidence bundle answers:

- what contract or lane is being claimed
- what engine or harness version was evaluated
- under what comparator class
- with which evidence files
- with which support tier and exemptions

It is the governance substrate for making honest claims.

---

## Shared semantics that must be frozen

A conforming evidence bundle must preserve:

- subject identity (engine / harness / lane)
- contract version
- scenario / lane id
- comparator class
- support tier
- evidence refs
- exemptions / caveats
- capture / evaluation timestamp

Without this, claims become narrative rather than auditable.

---

## Relation to replay and manifests

Replay session and conformance evidence are related but not identical.

- replay session = what the engine should be able to reproduce
- conformance evidence = what was actually evaluated and what was claimed
- engine conformance manifest = tracked registry of those claims

---

## Non-goals

This contract does not freeze:

- CI system choice
- markdown presentation style for reports
- dashboard formatting
- internal reviewer workflow

---

## Recommended first schema content

A future dedicated schema may exist, but the immediate requirement is that engine conformance manifests and evidence bundles use a stable shared vocabulary for:

- `engine_family`
- `engine_ref`
- `contract_version`
- `scenario_id`
- `comparator_class`
- `support_tier`
- `evidence_refs`
- `exemptions`
- `notes`

---

## Current owners

Current ownership is distributed across:

- target-harness freeze manifest discipline
- conformance docs under `docs/conformance/`
- replay evidence bundles
- the new `contracts/kernel/manifests/` scaffold
- future `conformance/engine_fixtures/` bundles

---

## Current evidence / tests

Current evidence is project-wide rather than centralized:

- E4 target freeze manifest
- strict replay probes
- parity evidence bundles
- runtime evidence docs
- fixture and schema export tests

The gap is not evidence scarcity. It is evidence standardization for the engine-contract program.

## Coordination evidence expectation

For the current coordination tranche, a conformance bundle should make the causal chain inspectable rather than merely summarizing the outcome.

The sparse supervisor-worker evidence should include:

- the accepted coordination signal
- the derived wake payload
- trigger lineage (`subscription_id`, `trigger_signal_id`, `trigger_code`, `source_task_id`)
- the review verdict record
- mission completion output when the supervisor validated `complete`

That keeps projection, coordination truth, and mission authority separable in the evidence itself.

The older sparse `supervisor_decision` fixture field remains acceptable only as compatibility-oriented explanatory evidence.

It should be interpreted as:

- a projection over the underlying `review_verdict` truth
- useful for legacy reference readability
- not a new durable coordination object

For the reviewed-result tranche, evidence should now make four layers inspectable:

- signal truth
- review/verdict truth
- any completion output or later directive truth
- projections such as wakeup notices

For the directive tranche, evidence should additionally preserve:

- the directive object itself
- the review-verdict-to-directive lineage
- whether the directive produced a downstream wake or remained control-only

For the reducer-style multi-worker tranche, evidence should also preserve:

- shard signals as separate producer truth
- reducer signal as the fan-in subject
- aggregate contract metadata or validation outcome
- supervisor review over reducer output
- directive lineage when blocked shard handling requires action intent

For the longrun/escalation tranche, evidence should additionally preserve:

- runtime-emitted `no_progress`, `retryable_failure`, or `human_required` signal truth
- the review verdict recorded over that runtime signal
- any resulting directive truth
- stop reason and longrun summary without collapsing those layers together

When longrun summaries expose coordination state, they should prefer the shared read-only coordination inspection snapshot and keep raw event traces separate as evidence, not fold those traces into a custom summary-specific coordination shape.

For the delegated-verification tranche, evidence should additionally preserve:

- worker result signal as the verified subject
- verifier signal carrying a verification-result contract
- subject-signal lineage inside that verification bundle
- supervisor review over verifier output
- any directive issued back to the verifier when verification fails

For the hardened intervention tranche, evidence should additionally preserve:

- the effective reviewer role profile used for review truth
- whether host intervention required a prior supervisor `escalate`
- any `support_claim_ref` that narrowed the allowed host action set
- the effective `allowed_host_actions` exposed in inspection

## E4 closure note

For current E4-facing public dossiers, coordination should remain narrow:

- mission ownership stays explicit
- reviewer roles stay named and small
- host override stays directive-based
- support-aware narrowing stays visible in policy/config rather than hidden in UI behavior

---

## Known ambiguities

Still unresolved:

- whether a dedicated `bb.conformance_evidence.v1` schema should exist separately from the manifest schema
- how much duplication between per-fixture metadata and the engine conformance manifest is acceptable
- how to classify partially delegated hybrid-engine results

---

## Immediate next steps

1. create the first populated engine conformance manifest instance
2. define the first fixture-family metadata convention
3. add validation that manifest rows point at real files
