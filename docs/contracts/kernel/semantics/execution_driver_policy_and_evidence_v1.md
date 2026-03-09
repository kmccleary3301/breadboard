# Execution Driver Policy And Evidence V1

## Purpose

This dossier defines the policy expectations that sit above concrete execution drivers and below kernel task/tool semantics.

The TypeScript kernel needs to be able to say, in shared contract language:

- how strong the requested security tier is
- what kind of side effects are expected from the chosen placement
- what evidence must come back before the execution is considered honest

Without this layer, a host bridge or driver can silently widen or weaken the effective execution model.

---

## Contract role

This dossier complements:

- `bb.execution_capability.v1`
- `bb.execution_placement.v1`
- `bb.sandbox_request.v1`
- `bb.sandbox_result.v1`

Those contracts define *what* is requested and *what* came back.

This dossier defines the policy expectations that explain how to interpret the request/result boundary:

- security tier
- side-effect scope
- evidence strictness

---

## Security-tier interpretation

The current shared interpretation is:

- `trusted_dev`
  - local development posture
  - no claim of hostile-code isolation
  - suitable for controlled workspace execution
- `single_tenant`
  - stronger isolation expected
  - suitable for one-tenant sandbox backends like OCI
- `multi_tenant`
  - strongest shared requirement in the current contract family
  - should map to backends that can honestly justify multi-tenant isolation claims

Security tier is *not* identical to placement class.

Examples:

- `trusted_dev` may still choose `local_process`
- `single_tenant` may choose `local_oci`
- `multi_tenant` may require a delegated backend or a stronger isolation tier than plain OCI

---

## Side-effect expectation model

Execution planning must record the expected side-effect scope for the chosen placement.

The current shared dimensions are:

- filesystem scope
- network scope
- process scope
- persistence scope

These expectations are not backend-private details. They are kernel-visible planning semantics.

They answer questions like:

- should this run be able to mutate the host workspace directly?
- are process spawns happening on the host or inside a sandbox boundary?
- should artifacts persist as host workspace files or backend-owned artifacts?

---

## Evidence expectation model

Execution planning must also record what evidence is required to consider the run valid.

The current shared expectations are:

- stdout reference requirement
- stderr reference requirement
- artifact reference requirement
- side-effect digest requirement
- evidence reference requirement
- usage metadata requirement

These expectations are driven by:

- `evidence_mode`
- placement class
- replay/conformance needs

The important principle is:

- low-evidence convenience runs may still exist
- but strict replay or audit modes must ask for enough evidence to make claims falsifiable

---

## Shared interpretation rules

1. Placement is selected first in contract language.
2. Side-effect and evidence expectations are then derived from that placement and the requested capability.
3. Drivers may add backend-private detail, but they may not silently weaken the shared expectation.
4. If a driver cannot satisfy the requested expectation honestly, the kernel should surface an unsupported case or choose another placement.

---

## Current implementation status

The current TypeScript V2 tranche implements these expectations as driver-planning helpers inside:

- `sdk/ts-execution-drivers`

They are intentionally package-level runtime helpers rather than kernel schemas because they are execution-planning semantics derived from existing contracts.

If the project later needs cross-engine serialization of these expectations as first-class records, they can be promoted into dedicated schemas.

---

## Non-goals

This dossier does not attempt to standardize:

- every backend-specific cgroup/runtime knob
- every mount/network detail of OCI or microVM backends
- every forensic/audit artifact format

It only standardizes the kernel-visible planning expectations that must survive backend swapping.
