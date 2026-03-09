# Sandbox Envelopes V1

## Purpose

This dossier defines the shared kernel semantics for sandbox request and result envelopes.

The kernel must be able to ask an execution driver or heavy service to run work without that driver redefining kernel truth. The request/result envelope is the shared boundary for that interaction.

---

## Contract role

`bb.sandbox_request.v1` and `bb.sandbox_result.v1` sit between:

- kernel execution planning
- execution-driver backend implementation
- artifact/evidence collection
- host-visible fallback or error reporting

These contracts are not provider/runtime contracts and are not host bridge contracts. They are backend execution contracts.

---

## Shared semantics that must be frozen

A sandbox request should be able to describe:

- `request_id`
- capability snapshot or reference
- placement target or requested placement class
- workspace/mount refs
- rootfs/image/snapshot refs
- network policy summary
- secret refs / secret handling mode
- command or execution descriptor
- timeout and resource budget
- evidence mode

A sandbox result should be able to describe:

- `request_id`
- final status
- execution placement actually used
- stdout/stderr refs or inline summaries
- artifact refs
- side-effect digest
- usage / resource / latency metadata
- evidence refs
- structured failure information when relevant

---

## Why this matters

This contract is the boundary that allows BreadBoard to:

- run through OCI backends
- run through stronger OCI isolation like gVisor/Kata
- run through microVM or remote execution services
- keep the semantic engine above the backend

---

## Non-goals

This family does not attempt to standardize:

- every field exposed by container runtimes
- every VM lifecycle detail
- every backend-specific networking or mount primitive

Those stay backend-private as long as the request/result envelope can represent the kernel-relevant outcome.

---

## Immediate next steps

1. add request/result schemas
2. add minimal examples
3. define the first execution-driver package interfaces around these envelopes
4. add evidence/result fixture families once a real backend exists
