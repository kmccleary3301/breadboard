# Kernel Event Family Registry V1

This document records the doctrine for classifying runtime event families in the
multi-engine program. The machine-readable source of truth is
`contracts/kernel/registries/kernel_event_kinds.v1.json`, validated by
`contracts/kernel/schemas/bb.registry.v1.schema.json`.

## Machine-readable registry

Use `contracts/kernel/registries/kernel_event_kinds.v1.json` for the canonical
runtime event-kind IDs, status, kernel-family/projection-family binding, actor,
visibility, and bridge classification metadata.

Do not maintain a divergent literal event-kind list in this document. If a new
event type is introduced and expected to matter for replay, conformance,
transcript derivation, or alternative-engine work, add it to the registry before
it is treated as kernel truth.

## Classification rules

- `metadata.classification: "kernel"` means the event kind is a kernel-owned
  truth surface.
- `metadata.classification: "projection_only"` means the event kind exists for
  current client convenience and is not yet a kernel truth surface.
- `metadata.classification: "stream_only_bridge"` means the event kind is a
  projection/transport artifact.
- `metadata.classification: "host_only_bridge"` means the event kind is useful
  host-facing lifecycle/control surface, but not a kernel event-family truth
  surface.

## Notes on current owners

- `limits_update` is emitted as a best-effort host-facing bridge event from
  provider runtime code when provider rate-limit headers are parsed. It is
  intentionally not treated as kernel truth.
- `provider_response` is kernel truth for the scoped TS program because both the
  Python reference fixtures and the TS kernel/provider-aware execution slices
  rely on a shared host-visible provider exchange event family.
- long-running macro events are currently collapsed through
  `SessionState.record_lifecycle_event(...)`, so their cross-engine contract
  surface today is `lifecycle_event`, not a separate macro-event family.
- replay-only events like `completion` and `run_finished` are bridge/session
  orchestration surfaces, not kernel event families.

## Legacy or still-unclassified events

Runtime event kinds absent from
`contracts/kernel/registries/kernel_event_kinds.v1.json` should not be relied on
as cross-engine truth until they are promoted into the registry.
