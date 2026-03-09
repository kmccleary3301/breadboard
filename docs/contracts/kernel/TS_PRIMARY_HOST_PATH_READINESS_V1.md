# TS Primary Host Path Readiness V1

This note records the shipped boundary for the current V2 TypeScript runtime program and the remaining gap beyond the supported **Python-free primary host path**.

## What is already true

The repository now has all of the following in tracked form:

- a shared kernel contract program
- a Python reference interpreter
- a cross-engine conformance gate
- a real TypeScript kernel substrate
- an execution capability / placement layer
- a driver family for:
  - trusted local execution
  - OCI-backed execution
  - delegated remote execution
- an OpenClaw host bridge with frozen supported slices:
  - text lane
  - transcript continuation lane
  - narrow tool-bearing lane
  - provider-quirk preservation lane
  - direct trusted-local execution
  - OCI-backed execution through runtime adapters
  - delegated remote execution through a remote adapter boundary

## What "Python-free primary host path" means here

For the purposes of the V2 program, a Python-free primary host path means:

1. a TypeScript host can execute its supported slice entirely through the TS kernel and TS driver family
2. no Python process is required on the primary happy path for those supported slices
3. delegated heavy services are still allowed, but they are treated as backend services rather than as the semantic engine

This does **not** mean:

- BreadBoard has replaced every Python-backed service
- BreadBoard has reimplemented gVisor, Firecracker, or Ray in TypeScript
- every host/runtime surface is now TypeScript-native

## Remaining limits beyond the supported host path

### 1. Stronger backend realism

The driver family is real, but the strongest delegated/backend-mediated paths are still deliberately narrow:

- OCI-backed execution is supported through runtime adapters and tested command execution
- delegated remote execution is supported through a fetch-compatible adapter boundary
- stronger microVM or long-running delegated paths are not yet implemented as first-class runtime flows

### 2. Broader host slice width

OpenClaw is now credible as a proving ground, but still intentionally narrow:

- one narrow tool-bearing lane
- selected provider quirks
- transcript continuation
- explicit fallback

It is not yet a broad host-runtime replacement.

### 3. Durable orchestration backend proof

Temporal is now the chosen durable orchestration direction, and the repo now has a real adapter package for:

- workflow start descriptors
- workflow control-plane descriptors
- resume/update descriptors driven by transcript continuation patches

That is enough for architectural honesty and for a bounded heavy-service story, but not yet enough to claim a complete Python-free heavy-service replacement across every backend/runtime surface.

## Current readiness assessment

For the currently frozen OpenClaw-supported slices:

- a Python-free primary host path is **real**
- the contracts are explicit
- the TS execution path is real
- the fallback boundary is honest

For broad host parity or infrastructure-heavy execution:

- the answer is still **not yet**

## Practical conclusion

The project is now at the point where it can honestly say:

- BreadBoard has a real TypeScript semantic engine path
- supported TypeScript host slices can run without Python in the primary path
- stronger isolation and orchestration remain backend concerns behind explicit contracts

That is the right stopping line for the current tranche.
