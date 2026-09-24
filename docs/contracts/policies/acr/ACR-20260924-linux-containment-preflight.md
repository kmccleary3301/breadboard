# ACR-20260924-linux-containment-preflight

- `acr_id`: `ACR-20260924-linux-containment-preflight`
- `title`: Typed Linux containment denials and cancellation-safe trusted-process teardown
- `author`: BreadBoard E4 containment lane
- `date`: 2026-09-24
- `status`: implemented; independent exact-head review pending

## 1) Problem Statement

On Ubuntu hosts restricting unprivileged user namespaces, attested lease creation can fail while writing the child namespace map. Preflight fork exhaustion and later mount-view denial require distinct typed errors, not raw `OSError` or an unrelated `runtime_unsupported` classification. Cancelling termination after a background reader consumes a signed teardown frame must not discard the outcome or strand descriptors and a durable lease record. Linux recovery fixtures must release the original owner lock when modeling process death.

## 2) Scope and Surfaces

- Trusted-process attested Linux lease preflight, map-denial classification, and termination serialization in `breadboard.rl.harness`.
- Linux containment integration test guard, namespace-denial regression, tmpfs-bound assertion, and cold-recovery fixture ownership.
- Danger-zone: yes. No changes to isolation promotion, privileged deployment, kernel settings, model-visible capabilities, provider behavior, or the qualification lane.

## 3) Coupling and Generalization Impact

The preflight exercises the launcher's namespace and UID/GID-map setup before admitting a workload. Namespace-map denial returns `SandboxLaunchError(code="runtime_unsupported")`; preflight fork exhaustion returns `envelope_resources_exhausted`, and post-preflight mount-view permission denial returns `envelope_mount_denied`. The launcher carries errno and phase in its error frame; unrelated EACCES must not masquerade as a namespace-map denial. The envelope and handle each own a shielded teardown task: cancelling a caller propagates `CancelledError` while later callers await the same signed result. Descriptors close exactly once from the task's `finally`. Lease-owner locks remain authoritative: an expired record owned by a live process remains quarantined as `live_owner`. No recovery signal is sent to an unverified process group, including after PID reuse.

## 4) Change Classification

- Classification: `additive`.

Corrective, fail-closed change. No persistence schema or public plan change; separate typed denial codes identify unsupported namespaces, mount denial and resource exhaustion. No capability or readiness claim is inferred from a skipped sealed test.

## 5) Evidence and Validation Plan

Focused macOS runtime and process-integration tests; on local-vm-linux, fresh committed-head root and unprivileged suites under both default userns restriction and lifted restriction. Probe cancellation immediately after consuming a signed teardown frame; require a later terminate and close to reuse the verified outcome and release every descriptor and record. Probe preflight EAGAIN and post-preflight mount EACCES for distinct typed codes with no child effects. Default namespace restriction must skip sealed tests only with the typed unsupported-host reason and zero raw namespace setup `OSError`; lifted restriction executes sealed tests. Preserve exact head, suite outputs and SHA-256 hashes for independent review; run the danger-zone ACR guard. The VM is local evidence, not DO-2 qualification.

## 6) Rollout Plan

Require exact-head independent correctness/security review and applicable CI before protected merge. Do not mark Linux containment qualified or release an artifact on this ACR alone.

## 7) Rollback Plan

Revert this corrective commit through normal protected review if typed rejection, teardown truthfulness or owner-lock safety regresses. Keep attested lease admission disabled on an unsupported host rather than bypassing namespace setup. Retain evidence and any quarantined lease records; no migration is required.

## 8) Approvals

Independent exact-head containment reviewer required. Final decision remains with the campaign owner after CI and installed qualification; this document grants no merge authority.
