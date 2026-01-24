# Extension Middleware Spine (Draft)

This document defines the **canonical middleware phases**, **deterministic ordering**, and **policy decision lattice** for BreadBoard’s extension system. It is intended to be a stable contract for internal evolution and external SDK/plugin authors.

## Goals
- Deterministic behavior across parity replays.
- Stable extension points for plugins, guardrails, hooks, and context engines.
- Strict SSE ordering (monotonic `seq`).
- Clear conflict resolution and auditability.

## Canonical Phases (Fixed Order)
All extensions must execute within one of these phases. The phase order is fixed and must not change without an explicit protocol/version bump.

1. `on_session_init(ctx)`
2. `on_turn_start(ctx)`
3. `before_model(ctx)`
4. `after_model(ctx)`
5. `before_tool(ctx)`
6. `after_tool(ctx)`
7. `before_emit(event_ctx)`
8. `on_turn_end(ctx)`
9. `on_session_end(ctx)`

## Deterministic Ordering Within a Phase
Within each phase, middleware is ordered by:

1) Explicit dependency constraints (`before`, `after`) if provided.
2) Numeric `priority` (lower runs earlier).
3) Stable lexical order of `plugin_id` as a tie-breaker.

If a dependency cycle exists, the session must fail at initialization with a clear error.

## Policy Decision Lattice
Each guardrail/policy/middleware may emit a `PolicyDecision`:

- `ALLOW`
- `DENY`
- `TRANSFORM`
- `WARN`
- `ANNOTATE`

Merge rules (deterministic):

1) Any `DENY` blocks the action (short-circuit for behavior, but continue for audit if needed).
2) `TRANSFORM` decisions apply in deterministic order; conflicting transforms must error unless explicitly allowed.
3) `WARN` and `ANNOTATE` are accumulated and emitted as audit metadata.

Each decision must include:
- `reason_code` (stable string)
- `message` (human-readable)
- `audit` metadata (optional)

## Determinism Constraints
- `seq` is the sole ordering key for SSE events.
- No background emissions may interleave into the stream without a deterministic boundary.
- All extension ordering must be derivable from config + plugin IDs (no filesystem/import order).

## Parity Mode (High-Level)
A strict parity profile must:
- Disable non-essential middleware or extra event emissions.
- Freeze tool schemas and permission prompt formatting.
- Avoid any new fields/events in parity-bound streams.

## Reference Implementation
Canonical phases + ordering live in `agentic_coder_prototype/extensions/spine.py` and are used by the runtime hook manager for deterministic ordering.
Legacy hook phase mapping is documented in `docs/HOOK_PHASE_MAPPING.md`.
Not every canonical phase is invoked everywhere yet; new phases should be rolled out behind config gates.

## Status
Draft — implementers should treat this as the canonical specification unless superseded by a versioned policy document.
