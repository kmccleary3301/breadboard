# Kernel Schema Pack

This directory will hold machine-readable schemas for the shared BreadBoard kernel contract.

These schemas are intentionally being introduced gradually. A schema appearing here means:

- it belongs to the shared kernel contract program
- its shape is stable enough to start validating against
- its semantics should also have a paired human-readable dossier under `docs/contracts/kernel/semantics/`

A schema appearing here does **not** mean every detail of the associated semantics is complete.

---

## Current status

This is a first-pass scaffold.

The earliest schemas should stay narrow and focus on:

- stable envelope shape
- required identifiers and lineage fields
- top-level payload boundaries
- explicit versioning

Detailed payload and lifecycle semantics will continue to mature in lockstep with their dossiers and fixture bundles.

---

## Current first-pass schema set

Draft scaffold schemas now exist for:

- `bb.kernel_event.v1`
- `bb.session_transcript.v1`
- `bb.tool_spec.v1`
- `bb.tool_call.v1`
- `bb.tool_execution_outcome.v1`
- `bb.tool_model_render.v1`
- `bb.run_request.v1`
- `bb.run_context.v1`
- `bb.provider_exchange.v1`
- `bb.permission.v1`
- `bb.replay_session.v1`
- `bb.task.v1`

These should be read as contract-pack scaffolds, not claims of semantic completeness.
