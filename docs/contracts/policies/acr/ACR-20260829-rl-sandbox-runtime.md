# ACR-20260829-rl-sandbox-runtime

- `acr_id`: `ACR-20260829-rl-sandbox-runtime`
- `title`: Canonical fail-closed RL sandbox runtime
- `author`: Codex (RL integration closure)
- `date`: 2026-08-29
- `status`: implemented

## 1) Problem Statement

The RL harness needs one production sandbox contract. Historical factories silently selected a local process when a requested runtime was unknown or unavailable, and Docker episodes did not expose the complete persistent file, diff, cancellation, identity, and cleanup surface required by installed headless workers.

## 2) Scope and Surfaces

- Canonical `breadboard.rl.harness.sandbox` lease and factory interfaces.
- Persistent Docker execution rooted at `/testbed`, including bounded command and file operations.
- Explicit isolation and support status for Docker, gVisor, Firecracker, and process adapters.
- Historical process factories restricted to explicit development use.
- Danger-zone: yes.
- Outside scope: host Docker socket access by untrusted workloads, implicit image pulls, production process fallback, and promotion of gVisor or Firecracker to ready.

## 3) Coupling and Generalization Impact

- Core to extension dependency introduced: no.
- Runtime-specific authority is isolated behind the canonical sandbox lease.
- Generalization impact: positive; callers select one named adapter and receive its declared capabilities or a typed unavailable result.
- Coupling risk: medium because Docker lifecycle, materialization, tool file access, and cleanup receipts share one lease identity.

## 4) Change Classification

- Classification: `additive`.
- Compat reviewed: non-breaking.
- Production semantics intentionally fail closed where historical compatibility wrappers silently weakened isolation. The process adapter remains available only through explicit development mode.

## 5) Evidence and Validation Plan

- Canonical contract suite: create, execute, persistent file access, workspace diff, timeout, cancellation, process-tree termination, concurrent sessions, destroy, and unavailable-driver behavior.
- Docker security plan assertions: immutable digest, no implicit pull, explicit UID/GID, network selection, CPU/memory/PID/output bounds, ownership labels, and exact-resource cleanup.
- Packaged `SANDBOX_CAPABILITY_MATRIX.json` must load from a clean installed wheel.
- Docker may be marked ready only after the Linux official SWE image canary proves `/testbed`, base commit, diff, cancellation, and resource absence.
- Exact-head correctness, security, automated review, danger-zone ACR guard, and full CI are required before merge.

## 6) Rollout Plan

1. Merge the canonical interface and capability matrix after exact-head review and CI.
2. Bind the installed headless runner to this interface without a compatibility fallback.
3. Run the official SWE Docker canary on Linux before setting Docker or RL readiness true.
4. Keep gVisor experimental and Firecracker unsupported until runtime-specific live evidence exists.

No release, signing, notarization, direct `main` push, or unrestricted Docker socket exposure is authorized by this record.

## 7) Rollback Plan

- Revert this PR as one unit before reverting its kernel/execution dependencies.
- Remove any capability receipt generated from the reverted identity.
- Do not restore production process fallback; return a typed unavailable error until the canonical runtime is restored.

## 8) Approvals

- Sandbox correctness reviewer: required on the exact PR head.
- Sandbox security reviewer: required on the exact PR head.
- Final decision: owner approval required after CI and independent review; this ACR does not authorize merge by itself.
