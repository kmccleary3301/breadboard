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
