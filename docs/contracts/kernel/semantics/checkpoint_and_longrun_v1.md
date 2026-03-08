# Checkpoint And Longrun V1

## Purpose

This dossier defines the shared contract boundary for checkpointing and long-running run state.

The key requirement is honesty: the contract should freeze only the parts of checkpoint and longrun state that must be shared across engines, while allowing infra-heavy implementation details to remain engine-private or delegated.

---

## Contract role

This family covers:

- resumable run identity
- checkpoint metadata
- declared continuation state
- longrun episode / branch linkage
- delegated-service references where needed

It does **not** attempt to standardize all storage details on the first pass.

---

## Shared semantics that must be frozen

A conforming engine must preserve at least:

- checkpoint identity
- parent run / session identity
- checkpoint reason / label
- minimal resumable run-state metadata
- longrun episode identity where enabled
- branch linkage where enabled
- references to delegated backing state when actual state is external

---

## Delegation rule

Checkpoint and longrun semantics will likely be hybrid for some time.

That is acceptable as long as the shared contract cleanly distinguishes:

- portable semantic state
- delegated engine-private backing state
- opaque refs to delegated services

This rule is especially important for a TS engine that initially delegates sandbox or heavier orchestration to Python-backed services.

---

## Non-goals

This contract does not freeze:

- on-disk checkpoint file format in this tranche
- Ray checkpoint payloads
- full sandbox snapshot contents
- Python-only persistence internals
- exact storage driver choice

---

## Current Python owners

Current ownership spans:

- checkpoint manager paths
- session runner persistence / rehydration paths
- longrun/RLM metadata stores
- `.breadboard` state and meta files

The current implementation is richer than the current shared contract. This dossier exists to decide the minimum common truth.

---

## Current evidence / tests

Evidence exists indirectly across:

- checkpoint-related runtime behavior
- longrun / RLM branch projection tests
- pending-permission restoration tests
- resumability and replay artifacts

The work now is to make that evidence line up with explicit shared semantics.

---

## Known ambiguities

Still unresolved:

- which checkpoint surfaces are mandatory for a TS MVP
- how much of longrun branch state belongs here versus `task_and_subagent_v1`
- how to version delegated checkpoint payload refs

---

## Immediate next steps

1. keep this dossier prose-only for the first tranche
2. identify the minimum checkpoint metadata set to elevate into schema form later
3. avoid pretending full checkpoint parity is solved before the delegated-service plan is mature
