# ACR-20260827-engine-d6-recovery

- `acr_id`: `ACR-20260827-engine-d6-recovery`
- `title`: Accepted D6 recovery and bounded runtime behavior
- `author`: Codex (engine PR handoff)
- `date`: 2026-08-27
- `status`: implemented

## 1) Problem Statement

The engine `main` branch does not contain the accepted D6 product/runtime tree. The original D6 commit object is no longer available in any preserved Git object store or from the remote, while the recovered tree and sealed evidence remain available. The recovery touches kernel danger-zone paths and must not merge without an explicit architecture and compatibility decision.

## 2) Scope and Surfaces

- Product CLI/runtime, engine session state and CLI bridge, provider/runtime boundaries, security isolation, SDK/contracts, and their tests.
- Subsequent bounded Ray environment controls, error-only Ray logging, and durable replay-head behavior.
- Danger-zone: yes.
- Outside scope: later SDK credential hardening, typed public-operation seams, TUI integration, RL amendment closure, and release publication.

## 3) Coupling and Generalization Impact

- Core to extension dependency introduced: no intentional dependency.
- Provider-specific coupling introduced into the public product boundary: no; provider differences remain behind provider/runtime seams.
- Default endpoint or lifecycle semantics altered: yes, as part of the accepted product cutover.
- Coupling risk: high because the recovered tree spans product/runtime boundaries.

## 4) Change Classification

- Classification: `breaking product cutover`.
- Compatibility window: no legacy compatibility shim; consumers move to the accepted D6 product/runtime contract.
- Historical constraint: retain recovery commit `73f501e10d617de1145e0e9c8e46f0d75e88b433`; do not fabricate the lost `2c89d1087bc688b8ff698be524fdf9460fac9927` intermediate history.

## 5) Evidence and Validation Plan

- Canonical D6 evidence manifest SHA-256: `b273cba052f8405f814c64045900d537db76b98501d9396bbdcc3505efad7a9f`.
- Required gates: generated SDK fixed point, public client manifest fixed point, focused lifecycle/durability tests, installed two-turn journey evidence, clean-consumer package evidence, danger-zone ACR guard, compatibility acknowledgement, and fresh exact-head independent review.
- Acceptance criteria:
  - generated SDK and public manifests are current at the PR head;
  - bounded runtime controls and durable replay semantics remain intact;
  - installed product restart/readback remains source-independent;
  - the PR is explicitly treated as a compatibility break rather than silently labelled non-breaking.

## 6) Rollout Plan

1. Merge only after required CI and exact-head review pass.
2. Merge dependent SDK/auth and public-operation PRs in order.
3. Freeze runtime inputs only after the final runtime-affecting merge.
4. Build SDK and engine distributions from the final clean frozen main.

No release, signing, notarization, or direct `main` push is authorized by this record.

## 7) Rollback Plan

- Revert dependent public-operation and SDK/auth PRs first, then revert this PR.
- Re-run lifecycle/durability checks and installed-package probes after rollback.
- Preserve custody snapshots and sealed evidence; never rewrite them to make rollback checks pass.

## 8) Approvals

- Kernel reviewer: required on the exact PR head.
- Contracts reviewer: required on the exact PR head.
- Final decision: owner approval required after CI and independent review; this ACR does not authorize merge.
