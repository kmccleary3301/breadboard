# ACR-20260924-native-worker-argv-finalization

- `acr_id`: `ACR-20260924-native-worker-argv-finalization`
- `title`: Launch sealed native workers with admitted interpreter arguments
- `author`: BreadBoard E4 OpenClaw implementation
- `date`: 2026-09-24
- `status`: implemented (generic argv launch; finalizer integration requires independent review)

## 1) Problem Statement

The OpenClaw pinned classifier loader must register before the worker imports its private supplier symbols. The trusted-process launcher historically launched only the executable and entrypoint, ignoring declared interpreter arguments such as `--import`. A generic admitted argument sequence must preserve existing Pi and OpenHands launches while allowing a measured, manifest-covered loader.

## 2) Scope and Surfaces

- Kernel modules: `breadboard/rl/harness/composition.py`, `breadboard/rl/harness/sandbox.py`; the native phase port and shared Conductor are affected by subsequent command-result finalization.
- Extension modules: OpenClaw worker and its sha-verified loader.
- Contract surfaces: installed native adapter argv, sealed source manifest closure, worker launch, and one-shot finalization after runtime cleanup.
- Kernel danger-zone: yes.

## 3) Coupling and Generalization Impact

- Core-to-extension dependency: no; the installed adapter carries admitted executable, entrypoint, import paths and their manifest digests.
- Cross-harness behavior: Pi, OpenHands, and other adapters without declared flags keep the same executable-plus-entrypoint argv.
- Default endpoint semantics: unchanged.
- Risk: high, because an unmeasured interpreter flag or import could escape the sealed native closure. Reject unsupported flags and unlisted imports; measure imported files again at launch.

## 4) Change Classification

- Classification: `behavioral-change` to installed native worker launch, without changing default arguments.
- Compatibility window: existing installed descriptors omit argv and retain their prior command.
- Schema bump: none; the additive optional argv declaration is admitted only under the existing exact descriptor validator.

## 5) Evidence and Validation Plan

- Focused native argv tests prove default command stability, measured `--import` arguments, and tampering rejection.
- OpenClaw worker and Conductor tests prove pinned cleanup result semantics for success, failure, and absent cleanup errors; Pi Conductor tests prove no shared-loop regression.
- Run `scripts/check_danger_zone_acr.py` against explicit changed files and request independent review of the exact candidate head.
- Acceptance: no hand-built cleanup envelope, no post-retirement fallback, and a fail-closed typed error if the one-shot pinned finalizer cannot run.

## 6) Rollout Plan

Review the staged generic argv launch first, then the one-shot finalizer port, then the supplier-derived command result. Keep each as a separate commit and validate each scoped contract before promotion.

## 7) Rollback Plan

Revert the reviewed commits in reverse order on a new branch if an installed worker cannot launch or finalization cannot prove pinned output. Preserve captured evidence and existing cleanup quarantine; rerun focused native stream and danger-zone checks on the rollback candidate.

## 8) Approvals

- Kernel reviewer: independent exact-head review required.
- Contracts reviewer: independent exact-head review required.
- Ops reviewer: required before installed promotion.
- Final decision: Main retains promotion authority; this ACR does not authorize merge.
