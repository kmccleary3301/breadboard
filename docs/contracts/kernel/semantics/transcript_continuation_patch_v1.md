# Transcript Continuation Patch V1

## Purpose

This dossier defines the kernel contract for transcript continuation patches returned to host-owned persistence layers.

This is especially important for OpenClaw-class hosts, where BreadBoard should be able to continue a transcript without taking over the host's persistence model.

---

## Contract role

A transcript continuation patch is the delta BreadBoard returns so that a host can keep its own transcript/session store coherent.

It should sit between:

- kernel event / transcript derivation
- host-owned transcript persistence
- replay/evidence expectations around continuation

---

## Shared semantics that must be frozen

The patch should be able to describe:

- `patch_id`
- `pre_state_ref`
- appended transcript items
- appended tool/event artifacts relevant to transcript continuity
- lineage updates
- compaction markers
- `post_state_digest`
- lossiness flags

This contract should remain independent from any one host's session manager or database schema.

---

## Non-goals

This contract does not replace:

- host session persistence
- host-specific thread/channel/message identifiers
- full transcript storage policy

It only describes the continuation delta the kernel is responsible for producing.

---

## Why this matters

Without this contract, each host bridge risks inventing its own transcript continuation semantics and the same run can behave differently across hosts.

---

## Immediate next steps

1. add schema and example
2. tie the OpenClaw host bridge to this contract more explicitly
3. later, add conformance fixtures that compare transcript continuation patches across engines
