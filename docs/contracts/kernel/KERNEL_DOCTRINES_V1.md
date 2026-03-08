# BreadBoard Kernel Doctrines V1

This document freezes the first doctrinal decisions for the BreadBoard multi-engine program.

These are not style preferences. They are the architectural rules intended to stop the TypeScript effort from becoming a shadow runtime or a projection-driven rewrite.

---

## Doctrine 1: One kernel contract, multiple interpreters

BreadBoard should be understood as:

- one runtime system
- one kernel contract
- multiple conforming interpreters
- multiple execution modes

This means:

- Python is not automatically the permanent source of truth for shared semantics
- TypeScript is not allowed to invent a parallel runtime truth
- shared semantics must be frozen in contract artifacts and evidence, not inferred from whichever implementation happens to exist first

Corollary:

- a TS engine is acceptable
- a hidden TS mini-engine inside a UI or SDK is not

---

## Doctrine 2: Kernel event log is canonical

The canonical runtime truth should be the kernel event log.

Why:

- it preserves causal ordering
- it supports replay and conformance
- it allows transcript/session state to be derived rather than hand-maintained in multiple incompatible ways
- it avoids overfitting to a single client or host projection

Implications:

- event families and ordering rules must become explicit contracts
- event visibility should distinguish model-visible, host-visible, and audit-only semantics where needed
- client projection streams must derive from kernel events rather than define them

---

## Doctrine 3: Transcript/session state is derived, not primary

Session and transcript state should be treated as normalized derived state built from kernel events.

That means:

- transcript state is not the primary truth source
- logger formatting is not transcript truth
- UI reducer state is not transcript truth
- host projections are not transcript truth

The transcript contract should focus on:

- what the model saw
- what stable lineage/order/provenance the system must preserve
- how tool calls and tool results appear in model-visible state
- how host-only annotations are separated from model-visible content

---

## Doctrine 4: Projection contracts are not kernel contracts

Projection contracts exist for:

- CLI bridges
- TUIs
- VSCode sidebars
- webapps
- desktop apps
- external hosts such as OpenClaw

These projections are important, but they are derived.

They must not become the kernel truth surface.

Reasons:

- projections differ by host needs
- projections often flatten or reshape events for convenience
- projections change faster than runtime semantics
- using them as engine truth makes cross-engine conformance much harder

Rule:

- no projection schema or reducer format should be treated as the canonical engine ABI

---

## Doctrine 5: Tool execution and tool rendering are separate contracts

A single "tool result" blob is not sufficient.

The kernel must distinguish:

- tool specification
- tool call
- raw execution outcome
- model-visible render

Why:

- replay integrity depends on preserving execution semantics
- parity integrity depends on preserving model-visible formatting semantics
- engines can execute the same tool correctly while still drifting in what the model is shown

Therefore:

- tool rendering policy is a first-class shared contract surface
- truncation, summarization, and visible formatting are not incidental implementation detail

---

## Doctrine 6: No parity or conformance claim without tracked evidence

This is inherited directly from current BreadBoard E4 discipline.

Any future multi-engine conformance claim must be backed by:

- tracked fixture or replay evidence
- explicit comparator class
- explicit support boundary
- explicit version/contract metadata

No claim should be inferred from a successful demo.

---

## Doctrine 7: Hosts pressure-test the kernel, but do not define it

External hosts and proving grounds such as OpenClaw are important.

But they must not become the place where kernel semantics are invented ad hoc.

Correct use of a host proving ground:

- test whether the kernel is actually usable
- test whether projections and runtime seams are sufficient
- identify under-specified contract areas

Incorrect use:

- letting a host-specific seam become the canonical event/session/tool ABI

---

## Doctrine 8: Heavy services may remain delegated initially

The early TS engine is not required to own every heavy execution surface.

Examples likely to remain delegated at first:

- sandbox execution
- Firecracker / VM-heavy isolation surfaces
- Ray/distributed orchestration
- some checkpoint/recovery-intensive infrastructure

The requirement is not TS-native ownership of everything.
The requirement is explicit delegation contracts that do not leak Python internals into kernel truth.

---

## Doctrine 9: Schemas are necessary but not sufficient

JSON Schemas are required for machine validation.

But schemas alone do not capture:

- lifecycle semantics
- ordering guarantees
- rendering rules
- replay strictness
- permission/approval behavior
- what is model-visible vs host-visible

Therefore every contract family should eventually have:

- machine-readable schema
- semantic dossier
- examples
- conformance fixtures

---

## Doctrine 10: Change the contracts before changing the engines

When shared semantics change, the correct order is:

1. contract doctrine / semantic dossier
2. schema or machine-readable contract artifact
3. fixture/evidence updates
4. engine implementation changes

Doing this in reverse is how multi-engine systems rot.
