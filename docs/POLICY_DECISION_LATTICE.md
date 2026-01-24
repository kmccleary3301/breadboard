# Policy Decision Lattice (Draft)

This document defines the canonical **policy decision actions** and **merge rules** used by guardrails, middleware, and plugins.

## Actions

Each policy decision emits one of the following actions:

- `ALLOW`: allow the operation as-is.
- `DENY`: block the operation (must carry a reason).
- `TRANSFORM`: mutate the operation payload in a deterministic way.
- `WARN`: allow, but emit a warning for audit/UX.
- `ANNOTATE`: allow, but attach metadata for audit/UX.

## Required Fields

Every decision must include:

- `reason_code`: stable identifier for analytics and audits.
- `message`: human-readable rationale (may be empty).
- `plugin_id`: owning plugin/guardrail id (may be empty for engine defaults).
- `priority`: numeric ordering hint (lower runs earlier).

## Deterministic Merge Rules

Merge rules must be deterministic and stable across replays:

1) **Any `DENY` wins**. If multiple denies exist, the earliest by deterministic ordering wins.
2) `TRANSFORM` actions apply in deterministic order. Conflicts are errors unless explicitly resolved.
3) `WARN` and `ANNOTATE` are accumulated and attached to the audit trail.
4) If no denies, the final action is `ALLOW`.

## Deterministic Ordering

Decisions are ordered by:

1) `priority` (lower first)
2) `plugin_id` (lexical)
3) `decision_id` (lexical; optional)
4) `reason_code` (lexical)

## Reference Implementation

See `agentic_coder_prototype/extensions/policy.py` for the current merge helper.

Runtime notes:
- Hooks may emit `policy_decisions` in their `HookResult.payload` (list of dicts).
- When `hooks.collect_decisions: true`, the hook manager collects these decisions and the conductor emits a merged summary into session metadata (`policy_decisions`).

## Status
Draft — decision collection is wired; enforcement/transform application is still evolving.
