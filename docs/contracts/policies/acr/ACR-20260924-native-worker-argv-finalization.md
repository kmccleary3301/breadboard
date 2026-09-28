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

- Core-to-extension dependency: no; composition reads the target's pinned compiled semantic binding and projects its sealed `native_worker.argv` to the installed adapter. An installed descriptor's optional argv can agree but cannot override it.
- Cross-harness behavior: Pi, OMP, and OpenHands targets without a sealed worker argv retain their existing executable-plus-entrypoint launch.
- Default endpoint semantics: unchanged.
- Risk: high, because an unmeasured interpreter flag or import could escape the sealed native closure. Admission rejects conflicting argv and missing manifest-covered imports, and launch measures imported files again.

## 4) Change Classification

- Classification: `behavioral-change` to installed native worker launch. A target with a sealed worker argv now supplies that command even if the installed descriptor omits argv.
- Compatibility window: targets without a sealed worker argv retain their prior installed arguments; an installed descriptor differing from a sealed argv fails admission.
- Schema bump: none; the compiled target and optional installed argv fields already exist.

## 5) Evidence and Validation Plan

- Focused native argv tests prove sealed-only projection, an agreeing descriptor, conflicting descriptor rejection, default command stability, measured `--import` arguments, and tampering rejection.
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
