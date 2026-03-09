# Unsupported Case V1

## Purpose

This dossier defines the shared kernel contract for unsupported-case classification.

The TS engine, Python reference engine, and host bridges need a shared way to say:

- why a requested lane is not supported
- whether fallback is allowed
- what degraded placement or behavior was used instead

---

## Contract role

This contract sits between:

- capability/placement resolution
- host bridge fallback logic
- public support-tier documentation
- conformance evidence and exemptions

It prevents unsupported behavior from being encoded only as ad hoc strings or host-specific branches.

---

## Shared semantics that must be frozen

An unsupported-case record should be able to express:

- `reason_code`
- human-readable summary
- affected contract family or lane
- whether fallback is allowed
- whether fallback was taken
- required capability that was unmet
- placement or backend that was unavailable
- evidence refs where relevant

---

## Non-goals

This contract should not become a dump of arbitrary backend exception strings. It should remain a small taxonomy suitable for support-tier claims and host fallback behavior.

---

## Immediate next steps

1. add schema and example
2. use it in host-bridge fallback classification over time
3. align future support-tier docs with this taxonomy
