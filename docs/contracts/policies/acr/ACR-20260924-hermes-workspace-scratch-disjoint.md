# ACR-20260924-hermes-workspace-scratch-disjoint

- `acr_id`: `ACR-20260924-hermes-workspace-scratch-disjoint`
- `title`: Require disjoint, not sibling, Hermes workspace and native scratch roots
- `author`: BreadBoard E4 campaign
- `date`: 2026-09-24
- `status`: implemented

## 1) Problem Statement

Since `c5d303ba`, the Hermes native worker (`hermes_worker.py`) and its shell boundary (`hermes_tool_exec.py`) have required `workspace.parent == scratch.parent`. The lease boundary places the workspace at `<episode>/workspace` and the native scratch at `<episode>/lease/<lease-id>.native-scratch` (`sandbox._native_scratch_path`, under the manager's `lease_root`). These paths are not siblings, so every installed Hermes case failed at initialize with `HermesWorkerError: workspace/scratch must be separate siblings`. DO-2 job 1217 hit this for all six cases before any request reached the receiver. The previous local tests never passed the lease's real initialize roots to the worker check.

## 2) Scope and Surfaces

- Kernel modules touched: `breadboard/rl/harness/hermes_worker.py` and `breadboard/rl/harness/hermes_tool_exec.py`. Each now has a module-level `_require_disjoint_roots` that replaces its inline sibling check.
- Extension modules touched: none.
- Contract surfaces touched: Hermes native initialize root admission. The native phase payload, sandbox scratch placement and shell authority policy fields are unchanged.
- Is this a **kernel danger-zone** change? `yes`.

## 3) Coupling and Generalization Impact

- Danger-zone: yes.
- Core -> extension dependency: no.
- Cross-harness parity narrowed: no. Other native profiles do not use these checks.
- Default endpoint semantics altered: no.
- Coupling risk: low. The new invariant is the one the isolation actually needs: neither resolved root may be equal to or an ancestor of the other (`Path.is_relative_to` checked both ways; it is reflexive). That keeps the workspace snapshot and Landlock workspace write root from covering scratch, and keeps scratch from covering the workspace. Sibling layouts still pass. Both checks still fail closed, with `HermesWorkerError` in the worker and `ValueError` in the shell boundary.
- Sibling-layout dependents: none. The shell authority builds its write/read roots from `workspace`, `scratch/shell-home`, `scratch/terminal`, `hermes_home` and the authority file, and never derives one root from the other's parent. `hermes_tools.HermesToolRuntime` treats the two roots independently.

## 4) Change Classification

- Classification: `internal` (native admission defect fix).
- Compatibility window: none. The product lease layout was always the intended input.
- Required schema/version bumps: none.

## 5) Evidence and Validation Plan

- Required contract lane tests: `tests/rl/harness/test_hermes_conductor_regressions.py` sends a Hermes initialize through `LeaseBackedRunnerWorkspace.invoke_native_phase` with lease root `<episode>/lease` and workspace `<episode>/workspace`. It asserts that the real scratch path is the non-sibling `<episode>/lease/<lease-id>.native-scratch`, then admits the captured roots through both checks. Sibling and name-prefixed sibling roots are admitted. Scratch inside workspace, workspace inside scratch and equal roots are rejected by both checks. At base `47b81448e19562fa7f659ff35d4861ab62e595f3` the new tests fail. After extracting the old predicate unchanged, the lease-layout test fails with the exact DO-2 error. After the fix they pass.
- Required replay/parity checks: Main owns the installed Hermes replay, which needs an image rebuilt with these files. This local change alone does not assert that the replay succeeds.
- Required conformance checks: `scripts/check_danger_zone_acr.py`, `scripts/check_kernel_contract_pack_v1.py`, and the Hermes/conductor harness tests.
- Required evidence bundles to refresh: none. No schema or golden artifact changes.
- Acceptance criteria: the product lease layout is admitted; nested and equal roots fail closed in both modules.

## 6) Rollout Plan

1. Run the focused tests and both contract guards.
2. Obtain independent review of the exact commit. Main handles push, image rebuild and installed replay.

- Flags/toggles: none.
- Blast radius: Hermes native worker initialize and shell boundary setup only.
- Monitoring hooks: root admission failures keep their fail-closed error types, with the messages `workspace/scratch must be disjoint` and `Hermes workspace and scratch must be disjoint`.

## 7) Rollback Plan

- Trigger: a Hermes isolation regression that involves workspace/scratch overlap.
- Rollback: revert both helper changes, the regression tests and this ACR together. Installed Hermes initialize would then fail again on the product lease layout.
- Artifact/state restoration: none. No persisted state changes.
- Post-rollback verification: rerun the Hermes regression tests and both contract guards.

## 8) Approvals

- Kernel reviewer: required on the exact candidate head.
- Contracts reviewer: required on the exact candidate head.
- Ops reviewer: Main handles the installed replay.
- Final decision: pending independent review. This ACR does not authorize push or merge.
