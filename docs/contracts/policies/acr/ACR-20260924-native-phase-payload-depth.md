# ACR-20260924-native-phase-payload-depth

- `acr_id`: `ACR-20260924-native-phase-payload-depth`
- `title`: Admit bounded native JSON Schema payloads at model-shaped JSON depth
- `author`: BreadBoard E4 campaign
- `date`: 2026-09-24
- `status`: implemented

## 1) Problem Statement

The native phase freeze rejected the registered OpenHands initialize payload before the first request: its sealed `native_config.tool_schemas` reaches payload depth 13, but `LeaseBackedRunnerWorkspace.invoke_native_phase` allowed only 8. `JsonSnapshotError` becomes `runtime_preflight_failed`. The installed replay jobs 1184/1189 exposed this admission failure; the previous local tests did not pass the registered config through this lease boundary. The regression test now uses that config and the same invocation path.

## 2) Scope and Surfaces

- Kernel modules touched: `breadboard/rl/harness/sandbox.py` (one shared native payload depth constant and its freeze call).
- Extension modules touched: none.
- Contract surfaces touched: native phase payload admission, for every native profile; no payload schema or recorded artifact format changes.
- Is this a **kernel danger-zone** change? `yes`.

## 3) Coupling and Generalization Impact

- Danger-zone: yes.
- Core -> extension dependency: no; the sandbox does not name a profile in the new depth rule.
- Cross-harness parity narrowed: no; the shared upper bound increases from 8 to 64.
- Default endpoint semantics altered: no.
- Coupling risk: low. This matches the existing model-shaped JSON convention, while the existing `max_nodes=observation_bytes+1` and `max_encoded_bytes=observation_bytes` checks are unchanged. Payloads at depth 65 remain rejected.
- Registered config exposure under the old depth-8 limit (payload includes `task` and `native_config`; config size is on-disk bytes): Pi 1,654 bytes/depth 6 (no); OpenHands 22,468/depth 13 (yes); OpenClaw 4,973/depth 6 (no); Hermes 29,162/depth 10 (yes); OMP 6,987/depth 7 (no). Dynamically generated runtime payloads may have different depths.

## 4) Change Classification

- Classification: `internal` (native admission defect fix).
- Compatibility window: none; deeper formerly invalid JSON now admits within the existing byte/node budgets.
- Required schema/version bumps: none.

## 5) Evidence and Validation Plan

- Required contract lane tests: `tests/rl/harness/test_sandbox_native_phase_admission.py` exercises the registered OpenHands config through lease invocation and asserts that a depth-65 input yields `runtime_preflight_failed`, with a depth `JsonSnapshotError` cause. The new test failed on base head `b8cf92e4859dcca251999ee6954f97e828653f90` and passed after the bound changed.
- Required replay/parity checks: Main owns the installed OpenHands replay; this local change alone does not assert its success.
- Required conformance checks: `scripts/check_danger_zone_acr.py`, `scripts/check_kernel_contract_pack_v1.py`, and focused harness tests.
- Required evidence bundles to refresh: none; there is no schema or golden-artifact change.
- Acceptance criteria: registered OpenHands initialize payload admits; depth 65 still fails preflight; current byte/node limits stay unchanged.

## 6) Rollout Plan

1. Run focused tests and both contract guards.
2. Obtain independent review of the exact commit; Main handles push and installed replay.

- Flags/toggles: none.
- Blast radius: shared native phase payload freeze, depth only.
- Monitoring hooks: native admission errors retain `runtime_preflight_failed`.

## 7) Rollback Plan

- Trigger: regression in native payload admission or memory bounds.
- Rollback: revert the sandbox constant/call change, regression test, and this ACR together after checking replay impact.
- Artifact/state restoration: none; no persisted schema changes.
- Post-rollback verification: rerun the native phase admission test and both contract guards; note OpenHands initialization would fail again.

## 8) Approvals

- Kernel reviewer: required on the exact candidate head.
- Contracts reviewer: required on the exact candidate head.
- Ops reviewer: Main handles installed replay.
- Final decision: pending independent review; this ACR does not authorize push or merge.
