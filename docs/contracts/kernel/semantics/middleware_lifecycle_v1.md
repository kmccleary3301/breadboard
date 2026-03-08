# Middleware Lifecycle V1

## Purpose

This dossier freezes the naming and semantic intent of the BreadBoard middleware / extension lifecycle as it relates to the shared kernel program.

---

## Canonical lifecycle names

The current canonical phase names are:

1. `before_turn`
2. `before_tool`
3. `after_tool`
4. `after_turn`
5. `before_model`
6. `after_model`

These names are drawn from current runtime behavior and the extension spine docs. Future engines must preserve the semantics of these lifecycle boundaries even if internal implementation structure differs.

---

## Current sources of truth

- `agentic_coder_prototype/conductor_execution.py`
- `agentic_coder_prototype/conductor_loop.py`
- `agentic_coder_prototype/extensions/spine.py`
- `docs/EXTENSION_MIDDLEWARE_SPINE.md`

---

## Shared semantics

A conforming engine must preserve:

- deterministic phase ordering
- explicit phase identity
- stable decision lattice semantics for deny/allow/transform where already frozen
- no reliance on import order or accidental registration order

---

## Non-goals

This dossier does not yet freeze the full hook payload schema. It freezes the phase names and their intended ordering so the cross-engine program does not drift.

---

## Immediate next steps

1. keep the extension spine docs aligned with these names
2. later add a dedicated middleware contract family if the TS engine needs one as a first-class schema pack surface
