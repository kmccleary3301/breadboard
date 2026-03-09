# Execution Capability And Placement V1

## Purpose

This dossier defines the shared kernel semantics for **execution capability** and **execution placement**.

This family exists because the kernel must be able to describe, in a serializable and cross-engine way:

- what kind of execution is being requested
- what security tier is required
- what placement classes are allowed
- what evidence level must come back
- which placement/backend actually satisfied the request

Without this family, host bridges, TS runtime work, and heavy-service delegation all remain under-specified.

---

## Contract role

These contracts sit above concrete execution backends and below host-specific UX.

They are kernel-truth candidates because they affect:

- which tools or steps may run at all
- whether local process, OCI, microVM, or remote execution is acceptable
- what artifact and evidence burden applies
- how the runtime explains unsupported or downgraded execution paths

---

## Shared semantics that must be frozen

A conforming engine must preserve:

### `bb.execution_capability.v1`

- requested `security_tier`
- requested `isolation_class`
- explicit file/network/program/env permission surface
- secret handling mode
- resource budget expectations
- evidence expectations
- whether interactive TTY-style execution is required

### `bb.execution_placement.v1`

- the placement actually chosen or proposed
- whether the placement is local, OCI-backed, microVM-backed, remote, or delegated
- whether the chosen placement satisfied or downgraded the requested capability
- the runtime/backend identifier responsible for execution

These contracts must remain serializable and comparable across engines.

---

## Security-tier doctrine

This family should make it possible to distinguish at least:

- `trusted_dev`
- `single_tenant`
- `shared_host`
- `multi_tenant`

The contract is not the security implementation itself. It is the kernel-level description of the execution promise being requested or met.

---

## Placement doctrine

The kernel should be able to express at least these placement classes:

- `inline_ts`
- `local_process`
- `local_oci`
- `local_oci_gvisor`
- `local_oci_kata`
- `local_microvm`
- `remote_worker`
- `delegated_python`
- `delegated_oci`
- `delegated_microvm`

This lets the TS engine remain the semantic owner even while concrete execution backends differ.

---

## Non-goals

This family does not freeze:

- exact Docker/containerd API calls
- exact Firecracker REST/Jailer lifecycle
- exact Kubernetes or Nomad orchestration semantics
- exact OCI mount syntax
- exact cloud/provider-specific execution APIs

Those belong to execution-driver packages or heavy-service implementations.

---

## Current owners and future owners

Current Python-side ownership is still distributed across:

- tool/runtime policy and permission layers
- sandbox request construction paths
- longrun and checkpoint metadata surfaces
- hybrid delegation docs rather than one explicit shared contract

The intended future owners are:

- `ts-kernel-core` for semantic ownership
- execution-driver packages for backend-specific implementation
- Python reference-engine adapters for reference behavior on delegated lanes

---

## Immediate next steps

1. add `bb.execution_capability.v1.schema.json`
2. add `bb.execution_placement.v1.schema.json`
3. add minimal examples for both
4. wire them into validator/tests
5. later, add execution-driver and sandbox envelopes that consume these contracts
