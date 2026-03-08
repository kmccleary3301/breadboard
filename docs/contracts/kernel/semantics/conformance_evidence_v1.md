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
