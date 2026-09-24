# ACR-20260924-linux-containment-preflight

- `acr_id`: `ACR-20260924-linux-containment-preflight`
- `title`: Typed Linux namespace preflight and serialized trusted-process teardown
- `author`: BreadBoard E4 containment lane
- `date`: 2026-09-24
- `status`: implemented; independent exact-head review pending

## 1) Problem Statement

On Ubuntu hosts restricting unprivileged user namespaces, attested lease creation reaches a raw permission error while writing the child namespace map. A termination race can repeat an envelope teardown and retain a sealed executable descriptor. Linux recovery tests also simulated a crashed lease owner while the original process still held its owner lock; that lock correctly prevented takeover.

## 2) Scope and Surfaces

- Trusted-process attested Linux lease preflight, map-denial classification, and termination serialization in `breadboard.rl.harness`.
- Linux containment integration test guard, namespace-denial regression, tmpfs-bound assertion, and cold-recovery fixture ownership.
- Danger-zone: yes. No changes to isolation promotion, privileged deployment, kernel settings, model-visible capabilities, provider behavior, or the qualification lane.

## 3) Coupling and Generalization Impact

The preflight exercises the launcher's namespace and UID/GID-map setup in a short-lived child before executing or admitting a workload. A rejected setup returns `SandboxLaunchError(code="runtime_unsupported")`, not an unconfined fallback. The actual envelope launch retains its own fail-closed denial classification because capability may change after preflight. Termination serializes concurrent callers so only one pidfd-backed teardown consumes the launcher receipt; subsequent calls observe the closed handle. Lease-owner locks remain authoritative: an expired record owned by a live process remains quarantined as `live_owner`. No recovery signal is sent to an unverified process group, including after PID reuse. The native scratch mount is quarantined when runtime identity cannot be proven.

## 4) Change Classification

- Classification: `additive`.

Corrective, fail-closed change. No persistence schema or public plan change; existing typed unsupported-host refusal is reused. No capability or readiness claim is inferred from a skipped sealed test.

## 5) Evidence and Validation Plan

Focused macOS runtime, process-integration and existing lease-envelope tests; on local-vm-linux, fresh committed-head root and unprivileged focused suites under both default userns restriction and lifted restriction. Verify that the default restriction skips sealed tests with the typed unsupported-host reason, with zero raw namespace setup `OSError`; the lifted restriction executes the sealed tests, including SIGKILL and stale PID-record recovery. Preserve exact head, suite outputs and SHA-256 hashes for independent review; run the danger-zone ACR guard against the changed-file manifest. The VM is local evidence, not DO-2 qualification.

## 6) Rollout Plan

Require exact-head independent correctness/security review and applicable CI before protected merge. Do not mark Linux containment qualified or release an artifact on this ACR alone.

## 7) Rollback Plan

Revert this corrective commit through normal protected review if typed rejection, teardown truthfulness or owner-lock safety regresses. Keep attested lease admission disabled on an unsupported host rather than bypassing namespace setup. Retain evidence and any quarantined lease records; no migration is required.

## 8) Approvals

Independent exact-head containment reviewer required. Final decision remains with the campaign owner after CI and installed qualification; this document grants no merge authority.
