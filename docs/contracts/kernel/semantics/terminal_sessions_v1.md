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
- host-facing terminal interaction failures should be shaped through `bb.unsupported_case.v1`
  rather than leaking raw driver/runtime exceptions when the session is already gone or the
  current backend cannot satisfy the requested interaction
- public dossiers/configs should expose terminal-session shape explicitly through a
  `terminal_sessions:` section, even when a given lane is only documenting rather than freezing
  that surface

## Current implementation notes

The first shipped runtime tranche now includes:

- shared terminal-session driver contracts in `@breadboard/execution-drivers`
- a trusted-local process-backed session manager in `@breadboard/execution-driver-local`
- kernel-core helpers that wrap begin / interaction / delta / end payloads into canonical kernel events
- a terminal registry reducer that remains a derived projection rather than canonical truth
- product-facing session views in `@breadboard/backbone` that keep support claims, status,
  snapshot state, and end-state summaries together
- host-facing session/end projection helpers in `@breadboard/host-kits`
- workspace terminal shaping helpers that turn terminal end payloads into host-usable
  artifact/evidence references without changing kernel truth

This is intentionally the first runtime slice, not the final parity claim:

- the trusted-local path is pipes-backed rather than PTY-backed
- stronger OCI / remote session drivers remain follow-on work
- Codex-background-terminal E4 claims still require frozen golden/conformance lanes on top of these primitives

Phase 14 now also includes a Codex-shaped reference-engine semantic evidence bundle for:

- start
- stdin continuation
- poll
- no-output poll
- list / registry projection
- multi-session listing
- mixed-state listing with one ended session carried forward
- cleanup
- cleanup after exit

The shipped host/product layer now goes beyond raw registry reduction:

- `@breadboard/backbone` can return live terminal session objects and multi-session views
- `@breadboard/host-kits` can project live registry views that preserve both active session
  summaries and recently ended session summaries
- `@breadboard/workspace` can shape terminal end payloads into artifact/evidence-friendly
  product views

The generic reference fixtures remain `draft-semantic`, but the Codex-shaped lanes now include
several `reference-engine` rows for:

- start
- stdin continuation
- poll
- no-output poll
- list / registry projection
- multi-session listing
- mixed-state listing
- cleanup
- cleanup after exit
- dead-session interaction failure shaping
- running-summary projection with one active session and multiple recently ended sessions

That means the surface has moved beyond "planned" and beyond pure parity-prep shape checking.

## Conformance status

The first Phase 14 conformance tranche now includes explicit terminal-session fixture families for:

- begin / start descriptor
- poll
- stdin continuation
- output delta
- end
- registry projection
- cleanup projection

These fixtures are now stronger than parity-prep shape checks: they are reference-engine semantic
lanes, but they are still not final Codex E4 goldens.

Codex-specific projection note:

- kernel `poll` should remain a first-class interaction kind
- a Codex-facing projection can still render that as an empty write/continue operation if needed
- the kernel should not collapse `poll` into `stdin` just to match one harness view
