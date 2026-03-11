# Terminal Sessions V1

## Purpose

This dossier defines the shared kernel contract for persistent terminal/process sessions.

This family exists because:

- one-shot command execution is not enough for Codex-style background terminals
- background tasks and subagents do not model persistent process I/O correctly
- hosts and replay layers need a stable, inspectable session lifecycle

## Contract role

The terminal-session family must describe:

- stable terminal session identity
- startup/open semantics
- output delta semantics
- interaction semantics (`stdin`, `poll`, `signal`)
- end-of-life semantics
- registry/listing as a derived projection
- cleanup/control outcomes

## Shared semantics that must be frozen

### 1. Terminal sessions are distinct from tasks

Tasks, subagents, and longrun branches may reference terminal sessions, but terminal sessions are their own identity/lifecycle family.

### 2. Three IDs may exist

The model must allow:

- kernel session id
- public handle
- backend handle

These are not the same thing.

### 3. Kernel truth is event-sourced

Kernel truth should be derived from begin / delta / interaction / end events rather than a mutable host registry object.

### 4. Poll is semantic

The kernel should represent `poll` as its own interaction kind. Empty-stdin-as-poll is a harness-specific projection choice, not kernel truth.

### 5. Registry is projection

Active-session listings, `/ps`-style outputs, and running-session summaries are derived projection surfaces, not canonical event families.

### 6. Startup grouping and causal attribution are distinct

For Codex-style background terminals we need both:

- `startup_call_id` for grouping the terminal under the originating start/exec call
- `causing_call_id` for attributing later write, poll, or signal interactions

Those should not be collapsed into one field.

## Non-goals

This dossier does not freeze:

- exact slash-command spellings
- exact UI panel layout
- OS PID plumbing
- exact PTY implementation

## Immediate schema implications

The first-pass schema set should likely include:

- `bb.terminal_session_descriptor.v1`
- `bb.terminal_output_delta.v1`
- `bb.terminal_interaction.v1`
- `bb.terminal_session_end.v1`
- `bb.terminal_registry_snapshot.v1`
- `bb.terminal_cleanup_result.v1`

## Relationship to existing contracts

- `bb.execution_capability.v1` and `bb.execution_placement.v1` still describe where/how a session may run
- `bb.sandbox_request.v1` / `bb.sandbox_result.v1` remain the one-shot command family
- task/subagent contracts should reference terminal sessions by lineage or wake-condition refs
- public dossiers/configs should expose terminal-session shape explicitly through a
  `terminal_sessions:` section, even when a given lane is only documenting rather than freezing
  that surface

## Current implementation notes

The first shipped runtime tranche now includes:

- shared terminal-session driver contracts in `@breadboard/execution-drivers`
- a trusted-local process-backed session manager in `@breadboard/execution-driver-local`
- kernel-core helpers that wrap begin / interaction / delta / end payloads into canonical kernel events
- a terminal registry reducer that remains a derived projection rather than canonical truth

This is intentionally the first runtime slice, not the final parity claim:

- the trusted-local path is pipes-backed rather than PTY-backed
- stronger OCI / remote session drivers remain follow-on work
- Codex-background-terminal E4 claims still require frozen golden/conformance lanes on top of these primitives

Phase 14 now also includes the first Codex-shaped parity-prep evidence bundle for:

- start
- stdin continuation
- poll
- no-output poll
- list / registry projection
- multi-session listing
- cleanup

Those fixtures are still `draft-semantic`, but they mean the surface has moved beyond "planned".

## Conformance status

The first Phase 14 conformance tranche now includes explicit terminal-session fixture families for:

- begin / start descriptor
- poll
- stdin continuation
- output delta
- end
- registry projection
- cleanup projection

These fixtures are parity-prep evidence, not yet final Codex E4 goldens.

Codex-specific projection note:

- kernel `poll` should remain a first-class interaction kind
- a Codex-facing projection can still render that as an empty write/continue operation if needed
- the kernel should not collapse `poll` into `stdin` just to match one harness view
