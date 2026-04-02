# Replay Session V1

## Purpose

This dossier defines the shared contract for replay sessions.

Replay is one of BreadBoard's strongest assets. It is the mechanism that allows:

- target-harness parity validation
- regression testing of strict lanes
- future engine-vs-engine comparison
- frozen examples for host integrations

The replay-session contract must therefore be treated as a kernel-level truth surface, not an ad hoc test artifact.

---

## Contract role

A replay session records enough model-visible and tool-visible state to allow an engine to deterministically re-run a lane under a declared comparator class.

It is not necessarily a raw transcript, and it is not necessarily a human-readable conversation log. It is an execution contract.

---

## Shared semantics that must be frozen

A conforming engine must preserve replay semantics for:

- ordered message turns
- tool call identity and order
- model-visible tool payloads where required by the lane
- tool result rendering where required by the lane
- completion / stop semantics
- associated evidence references and strictness class
- replay-mode substitutions and allowed nondeterminism declarations

Different lanes may compare different things, but the replay contract must declare which class is in force.

---

## Comparator-class relation

Replay is not one universal equality mode.

Different replay lanes may require:

- shape equality
- normalized-trace equality
- model-visible equality
- workspace side-effects equality
- projection equality

The replay-session contract must be able to declare which comparator class the lane expects.

---

## Non-goals

This contract does not freeze:

- ad hoc one-off test harness wrappers
- raw provider dumps as the replay session itself
- host-specific transcript presentation
- CI log formatting

Those may accompany a replay lane, but they are not the replay session contract.

---

## Recommended first schema content

A first-pass `bb.replay_session.v1` should include:

- `schema_version`
- `scenario_id`
- `lane_id`
- `comparator_class`
- `messages`
- `tool_results`
- `completion_summary`
- `evidence_refs`
- `strictness`
- `notes`

---

## Current Python owners

Current replay-related ownership is spread across:

- `agentic_coder_prototype/replay.py`
- replay-aware execution paths in `agentic_coder_prototype/conductor/execution.py`
- replay provider runtime surfaces
- conversion scripts under `scripts/`
- many parity scenarios and evidence bundles under `docs/conformance/`

The semantics are strong in practice, but the shared contract still needs to be made more explicit.

---

## Current evidence / tests

There is already extensive evidence here:

- target-harness replay conversions
- parity scenarios
- strict replay probes
- provider runtime replay tests
- many conformance docs and evidence bundles

This dossier exists to turn that operational discipline into a shared kernel contract family.

---

## Known ambiguities

Still unresolved:

- how to cleanly separate replay session from conformance evidence bundle
- how much provider-exchange detail belongs in replay versus linked evidence
- how to encode acceptable nondeterminism without weakening the contract too much

---

## Why this matters for TypeScript

A TS engine without replay equivalence discipline would not be a serious BreadBoard engine. Replay must be a first-class cross-engine contract from the start.

---

## Immediate next steps

1. add `bb.replay_session.v1.schema.json`
2. add a minimal replay-session example
3. add an engine-fixture family for replay session
4. later connect this contract to engine-vs-engine differential execution
